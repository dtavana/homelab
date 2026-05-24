#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "optparse"
require "yaml"

ROOT = File.expand_path("..", __dir__)
DEFAULT_TARGET = "nginx-system-nginx-ingress-nginx-controller.nginx-system.svc.cluster.local"
DEFAULT_VALUES_OUTPUT = File.join(ROOT, "infrastructure", "homelab", "pangolin", "values.yaml")
DEFAULT_PRIVATE_RESOURCES_FILE = File.join(ROOT, "infrastructure", "homelab", "pangolin", "private-resources.yaml")
PUBLIC_CLASS = "nginx"

options = {
  values_output: DEFAULT_VALUES_OUTPUT,
  private_resources_file: DEFAULT_PRIVATE_RESOURCES_FILE,
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
  opts.on("--private-resources-file PATH", "Load private Pangolin resources from PATH") do |value|
    options[:private_resources_file] = value
  end
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

def deep_stringify(value)
  case value
  when Hash
    value.transform_values { |nested| deep_stringify(nested) }
  when Array
    value.map { |nested| deep_stringify(nested) }
  else
    value
  end
end

def load_private_resources(path)
  return {} unless File.exist?(path)

  config = YAML.safe_load(File.read(path), aliases: true) || {}
  unless config.is_a?(Hash)
    raise "#{path} must contain a YAML mapping"
  end

  resources = config.fetch("private-resources", {})
  unless resources.is_a?(Hash)
    raise "#{path} private-resources must be a YAML mapping"
  end

  resources
end

hosts = []

Dir[File.join(ROOT, "{apps,infrastructure}", "homelab", "**", "*.yaml")].sort.each do |path|
  next if path.end_with?(".sops.yaml")
  next if path == options[:values_output]
  next if path == options[:private_resources_file]

  YAML.load_stream(File.read(path)) do |doc|
    collect_hosts_from_ingress(doc, hosts)
    collect_hosts_from_values(doc, hosts) if File.basename(path) == "values.yaml"
  end
end

excluded = options[:exclude_hosts].map { |host| normalize_host(host) }
no_auth = options[:no_auth_hosts].map { |host| normalize_host(host) }
private_resources = load_private_resources(options[:private_resources_file])

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
blueprint["private-resources"] = private_resources unless private_resources.empty?

blueprint_yaml = blueprint.to_yaml(line_width: -1).sub(/\A---\n/, "")

values = {
  "global" => {
    "podSecurityContext" => {
      "fsGroup" => 65_534,
      "fsGroupChangePolicy" => "OnRootMismatch"
    }
  },
  "newtInstances" => [
    {
      "name" => "main-tunnel",
      "enabled" => true,
      "auth" => {
        "existingSecretName" => "newt-main-tunnel-auth"
      },
      "acceptClients" => true,
      "configPersistence" => {
        "enabled" => true,
        "mountPath" => "/var/lib/newt",
        "fileName" => "config.json",
        "type" => "persistentVolumeClaim",
        "existingClaim" => "newt-main-tunnel-config"
      },
      "blueprintFile" => "/etc/newt/blueprint.yaml",
      "blueprintData" => blueprint_yaml
    }
  ]
}
values_file = "# Generated by scripts/generate-pangolin-blueprint.rb. Do not edit by hand.\n#{deep_stringify(values).to_yaml(line_width: -1).sub(/\A---\n/, "")}"

if options[:dry_run]
  puts values_file
else
  FileUtils.mkdir_p(File.dirname(options[:values_output]))
  File.write(options[:values_output], values_file)
  warn "Wrote #{options[:values_output]}"
end
