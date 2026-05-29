#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "optparse"
require "yaml"

ROOT = File.expand_path("..", __dir__)
DEFAULT_TARGET = "nginx-system-nginx-ingress-nginx-controller.nginx-system.svc.cluster.local"
DEFAULT_VALUES_OUTPUT = File.join(ROOT, "infrastructure", "homelab", "pangolin", "values.yaml")
DEFAULT_BLUEPRINT_INPUT = File.join(ROOT, "infrastructure", "homelab", "pangolin", "blueprint.yaml")
PUBLIC_CLASS = "nginx"

options = {
  values_output: DEFAULT_VALUES_OUTPUT,
  blueprint_input: DEFAULT_BLUEPRINT_INPUT,
  target: DEFAULT_TARGET,
  port: 80,
  method: "http",
  no_auth_hosts: ["hass.dtavana.dev"],
  exclude_hosts: ["pangolin.dtavana.dev"],
  dry_run: false
}

OptionParser.new do |opts|
  opts.banner = "Usage: scripts/generate-pangolin-blueprint.rb [options]"
  opts.on("--values-output PATH", "Write Newt Helm values to PATH") { |value| options[:values_output] = value }
  opts.on("--blueprint-input PATH", "Merge explicit blueprint resources from PATH") { |value| options[:blueprint_input] = value }
  opts.on("--target HOST", "Pangolin target hostname") { |value| options[:target] = value }
  opts.on("--port PORT", Integer, "Pangolin target port") { |value| options[:port] = value }
  opts.on("--method METHOD", "Pangolin target method") { |value| options[:method] = value }
  opts.on("--no-auth-hosts x,y,z", Array, "Hosts that bypass Pangolin auth") { |value| options[:no_auth_hosts] = value }
  opts.on("--exclude-hosts x,y,z", Array, "Hosts to exclude from blueprint") { |value| options[:exclude_hosts] = value }
  opts.on("--dry-run", "Print generated blueprint instead of writing") { options[:dry_run] = true }
end.parse!

def normalize_host(host)
  host.to_s.delete_prefix("*.").delete('"').delete("'").strip
end

def public_ingress?(config)
  return false unless config.is_a?(Hash)
  return false if config.key?("enabled") && config["enabled"] == false

  ingress_class = config["className"] || config["ingressClassName"]
  ingress_class == PUBLIC_CLASS
end

def hosts_from_host_entries(entries)
  Array(entries).map do |entry|
    case entry
    when Hash
      entry["host"]
    when String
      entry
    end
  end.compact
end

def collect_hosts_from_values(node, hosts)
  return unless node.is_a?(Hash)

  node.each do |key, value|
    if key == "ingress" && value.is_a?(Hash)
      hosts.concat(hosts_from_host_entries(value["hosts"])) if public_ingress?(value)

      value.each_value do |nested|
        hosts.concat(hosts_from_host_entries(nested["hosts"])) if public_ingress?(nested)
      end
    end

    collect_hosts_from_values(value, hosts) if value.is_a?(Hash)
    value.each { |item| collect_hosts_from_values(item, hosts) } if value.is_a?(Array)
  end
end

def collect_hosts_from_ingress(doc, hosts)
  return unless doc.is_a?(Hash)
  return unless doc["kind"] == "Ingress"
  return unless doc.dig("spec", "ingressClassName") == PUBLIC_CLASS

  Array(doc.dig("spec", "rules")).each do |rule|
    hosts << rule["host"] if rule.is_a?(Hash)
  end
end

def resource_key(host)
  host.gsub(/[^a-zA-Z0-9]+/, "-").gsub(/^-|-$/, "").downcase
end

def display_name(host)
  host.split(".").first.split("-").map(&:capitalize).join(" ")
end

def merge_blueprint!(blueprint, additions, source)
  return blueprint if additions.nil?
  raise "#{source} must contain a YAML mapping" unless additions.is_a?(Hash)

  additions.each do |section, resources|
    next if resources.nil?
    raise "#{source}: #{section} must contain a YAML mapping" unless resources.is_a?(Hash)

    blueprint[section] ||= {}
    duplicates = blueprint[section].keys & resources.keys
    unless duplicates.empty?
      raise "#{source}: duplicate #{section} keys: #{duplicates.join(", ")}"
    end

    blueprint[section].merge!(resources)
  end

  blueprint
end

def load_blueprint_file(path)
  return {} unless File.exist?(path)

  merged = {}
  YAML.load_stream(File.read(path)) do |doc|
    merge_blueprint!(merged, doc, path)
  end
  merged
end

hosts = []

Dir[File.join(ROOT, "{apps,infrastructure}", "homelab", "**", "*.yaml")].sort.each do |path|
  next if path.end_with?(".sops.yaml")
  next if path == options[:values_output]

  YAML.load_stream(File.read(path)) do |doc|
    collect_hosts_from_ingress(doc, hosts)
    collect_hosts_from_values(doc, hosts) if File.basename(path) == "values.yaml"
  end
end

excluded = options[:exclude_hosts].map { |host| normalize_host(host) }
no_auth = options[:no_auth_hosts].map { |host| normalize_host(host) }

hosts = hosts
  .map { |host| normalize_host(host) }
  .reject(&:empty?)
  .reject { |host| host.include?("*") }
  .reject { |host| host.end_with?(".internal.dtavana.dev") }
  .reject { |host| excluded.include?(host) }
  .uniq
  .sort

blueprint = {
  "public-resources" => hosts.to_h do |host|
    auth = no_auth.include?(host) ? { "sso-enabled" => false } : { "sso-enabled" => true }

    [
      resource_key(host),
      {
        "name" => display_name(host),
        "protocol" => "http",
        "full-domain" => host,
        "auth" => auth,
        "targets" => [
          {
            "hostname" => options[:target],
            "port" => options[:port],
            "method" => options[:method]
          }
        ]
      }
    ]
  end
}

merge_blueprint!(blueprint, load_blueprint_file(options[:blueprint_input]), options[:blueprint_input])

blueprint_yaml = blueprint.to_yaml(line_width: -1).sub(/\A---\n/, "").chomp
blueprint_block = blueprint_yaml.lines.map { |line| "      #{line}" }.join

values_file = <<~YAML
  # Generated by scripts/generate-pangolin-blueprint.rb. Do not edit by hand.
  global:
    podSecurityContext:
      fsGroup: 65534
      fsGroupChangePolicy: OnRootMismatch
  newtInstances:
    - name: main-tunnel
      enabled: true
      auth:
        existingSecretName: newt-main-tunnel-auth
      acceptClients: false
      configPersistence:
        enabled: true
        mountPath: "/var/lib/newt"
        fileName: config.json
        type: persistentVolumeClaim
        existingClaim: newt-main-tunnel-config
      blueprintFile: "/etc/newt/blueprint.yaml"
      blueprintData: |-
  #{blueprint_block}
YAML

if options[:dry_run]
  puts values_file
else
  FileUtils.mkdir_p(File.dirname(options[:values_output]))
  File.write(options[:values_output], values_file)
  warn "Wrote #{options[:values_output]}"
end
