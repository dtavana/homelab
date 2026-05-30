#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "yaml"

ROOT = File.expand_path("..", __dir__)
CATALOG_PATH = File.join(ROOT, "apps", "homelab", "homepage", "catalog.yaml")
OUTPUT_ROOT = File.join(ROOT, "apps", "homelab", "homepage", "generated")
PUBLIC_CLASS = "nginx"
INTERNAL_CLASS = "nginx-internal"

def normalize_host(host)
  host.to_s.delete_prefix("*.").delete('"').delete("'").strip
end

def display_name(host)
  host.split(".").first.split("-").map(&:capitalize).join(" ")
end

def ingress_enabled?(config)
  config.is_a?(Hash) && !(config.key?("enabled") && config["enabled"] == false)
end

def ingress_class(config)
  config["className"] || config["ingressClassName"]
end

def hosts_from_entries(entries)
  Array(entries).map do |entry|
    case entry
    when Hash then entry["host"]
    when String then entry
    end
  end.compact
end

def hosts_from_ingress_config(config)
  hosts_from_entries(config["hosts"]) + Array(config["host"])
end

def collect_values_ingresses(node, ingresses, source)
  return unless node.is_a?(Hash)

  node.each do |key, value|
    if key == "ingress" && value.is_a?(Hash)
      if ingress_enabled?(value) && ingress_class(value)
        hosts_from_ingress_config(value).each do |host|
          ingresses << {
            "host" => normalize_host(host),
            "class" => ingress_class(value),
            "source" => source
          }
        end
      end

      value.each_value do |nested|
        next unless nested.is_a?(Hash)
        next unless ingress_enabled?(nested) && ingress_class(nested)

        hosts_from_ingress_config(nested).each do |host|
          ingresses << {
            "host" => normalize_host(host),
            "class" => ingress_class(nested),
            "source" => source
          }
        end
      end
    end

    collect_values_ingresses(value, ingresses, source) if value.is_a?(Hash)
    value.each { |item| collect_values_ingresses(item, ingresses, source) } if value.is_a?(Array)
  end
end

def collect_ingress_doc(doc, ingresses, source)
  return unless doc.is_a?(Hash)
  return unless doc["kind"] == "Ingress"

  klass = doc.dig("spec", "ingressClassName")
  namespace = doc.dig("metadata", "namespace")
  Array(doc.dig("spec", "rules")).each do |rule|
    host = normalize_host(rule["host"]) if rule.is_a?(Hash)
    next if host.to_s.empty?

    backend = rule.dig("http", "paths", 0, "backend", "service", "name")
    ingresses << {
      "host" => host,
      "class" => klass,
      "namespace" => namespace,
      "app" => backend,
      "source" => source
    }
  end
end

def load_catalog(path)
  return {} unless File.exist?(path)

  YAML.load_file(path) || {}
end

def service_entry(discovered, override)
  host = discovered["host"]
  entry = {
    "icon" => override["icon"] || "mdi-web",
    "href" => override["href"] || "https://#{host}",
    "description" => override["description"] || "#{discovered["class"]} ingress"
  }
  entry["namespace"] = override["namespace"] || discovered["namespace"] if override["namespace"] || discovered["namespace"]
  entry["app"] = override["app"] || discovered["app"] if override["app"] || discovered["app"]
  entry["podSelector"] = override["podSelector"] if override.key?("podSelector")
  entry["target"] = override["target"] if override["target"]
  { override["name"] || display_name(host) => entry }
end

def services_yaml(services)
  grouped = services.group_by { |service| service.fetch("group") }
  ordered_groups = grouped.keys.sort_by { |group| [group == "Other" ? 1 : 0, group] }

  ordered_groups.map do |group|
    {
      group => grouped[group]
        .sort_by { |service| service.fetch("entry").keys.first }
        .map { |service| service.fetch("entry") }
    }
  end.to_yaml(line_width: -1).sub(/\A---\n/, "")
end

def bookmarks_yaml(catalog)
  bookmarks = catalog.fetch("common_bookmarks", {})
  bookmarks.to_yaml(line_width: -1).sub(/\A---\n/, "")
end

def settings_yaml(catalog, instance)
  settings = catalog.fetch("settings", {}).dup
  settings["title"] ||= instance == "public" ? "Homelab" : "Homelab Internal"
  settings["theme"] ||= "dark"
  settings["color"] ||= "slate"
  settings["headerStyle"] ||= "boxed"
  settings["instanceName"] = instance
  settings["hideVersion"] = true unless settings.key?("hideVersion")
  settings["quicklaunch"] ||= {
    "searchDescriptions" => true,
    "hideInternetSearch" => false,
    "showSearchSuggestions" => true
  }
  settings.to_yaml(line_width: -1).sub(/\A---\n/, "")
end

def widgets_yaml
  [
    {
      "kubernetes" => {
        "cluster" => { "show" => true, "cpu" => true, "memory" => true, "showLabel" => true, "label" => "cluster" },
        "nodes" => { "show" => true, "cpu" => true, "memory" => true, "showLabel" => true }
      }
    },
    { "resources" => { "backend" => "resources", "expanded" => true, "cpu" => true, "memory" => true, "disk" => "/" } },
    { "datetime" => { "text_size" => "xl", "format" => { "dateStyle" => "long", "timeStyle" => "short", "hour12" => true } } },
    { "search" => { "provider" => "duckduckgo", "target" => "_blank" } }
  ].to_yaml(line_width: -1).sub(/\A---\n/, "")
end

catalog = load_catalog(CATALOG_PATH)
homepage = catalog.fetch("homepage", {})
excluded_hosts = Array(homepage["exclude_hosts"]).map { |host| normalize_host(host) }
excluded_hosts += [
  normalize_host(homepage["public_host"] || "homepage.dtavana.dev"),
  normalize_host(homepage["internal_host"] || "homepage.internal.dtavana.dev")
]

ingresses = []
Dir[File.join(ROOT, "{apps,infrastructure}", "homelab", "**", "*.yaml")].sort.each do |path|
  next if path.end_with?(".sops.yaml")
  next if path.include?("/homepage/generated/")
  next if path == File.join(ROOT, "infrastructure", "homelab", "pangolin", "values.yaml")

  YAML.load_stream(File.read(path)) do |doc|
    collect_ingress_doc(doc, ingresses, path)
    collect_values_ingresses(doc, ingresses, path) if File.basename(path) == "values.yaml"
  end
end

overrides = catalog.fetch("overrides", {})
services = ingresses
  .select { |ingress| [PUBLIC_CLASS, INTERNAL_CLASS].include?(ingress["class"]) }
  .reject { |ingress| ingress["host"].empty? || ingress["host"].include?("*") }
  .reject { |ingress| excluded_hosts.include?(ingress["host"]) }
  .uniq { |ingress| [ingress["host"], ingress["class"]] }
  .map do |ingress|
    override = overrides.fetch(ingress["host"], {})
    visibility = override["visibility"] || (ingress["class"] == INTERNAL_CLASS ? "internal" : "public")
    {
      "visibility" => visibility,
      "group" => override["group"] || (ingress["class"] == INTERNAL_CLASS ? "Internal" : "Public"),
      "entry" => service_entry(ingress, override)
    }
  end

{
  "public" => services.select { |service| ["public", "both"].include?(service["visibility"]) },
  "internal" => services.select { |service| ["public", "internal", "both"].include?(service["visibility"]) }
}.each do |instance, visible_services|
  dir = File.join(OUTPUT_ROOT, instance)
  FileUtils.mkdir_p(dir)
  File.write(File.join(dir, "services.yaml"), services_yaml(visible_services))
  File.write(File.join(dir, "bookmarks.yaml"), bookmarks_yaml(catalog))
  File.write(File.join(dir, "settings.yaml"), settings_yaml(catalog, instance))
  File.write(File.join(dir, "widgets.yaml"), widgets_yaml)
  File.write(File.join(dir, "kubernetes.yaml"), "mode: cluster\ningress: true\n")
  File.write(File.join(dir, "docker.yaml"), "")
  File.write(File.join(dir, "custom.css"), "")
  File.write(File.join(dir, "custom.js"), "")
end

warn "Wrote Homepage config to #{OUTPUT_ROOT}"
