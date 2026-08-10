import JoeOSCore
import SwiftUI

/// Native JoeOS module renderer.
///
/// Renders a `ModuleManifest` through a trusted SwiftUI component registry.
/// Only a fixed set of widget types is ever rendered; anything else fails
/// safely (a labeled unavailable surface, never arbitrary code). This is the
/// iOS-side counterpart of the server module catalog.
public struct ModuleRenderer: View {
    let manifest: ModuleManifest

    public init(manifest: ModuleManifest) {
        self.manifest = manifest
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                if manifest.widgets.isEmpty {
                    ContentUnavailableView(
                        "No widgets",
                        systemImage: "square.grid.2x2",
                        description: Text(manifest.description.isEmpty
                            ? "This module has no declared widgets."
                            : manifest.description)
                    )
                } else {
                    ForEach(manifest.widgets) { widget in
                        render(widget)
                    }
                }
            }
            .padding()
        }
        .background(Color.joeOSCanvas)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(manifest.displayName.isEmpty ? manifest.id : manifest.displayName)
                .font(.system(size: 22, weight: .bold, design: .default))
                .foregroundColor(.white)
            if !manifest.description.isEmpty {
                Text(manifest.description)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            }
            if !manifest.requiredCapabilities.isEmpty {
                HStack(spacing: 6) {
                    ForEach(manifest.requiredCapabilities, id: \.self) { cap in
                        Text(cap)
                            .font(.caption2)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.joeOSCyan.opacity(0.18))
                            .foregroundColor(Color.joeOSCyan)
                            .clipShape(Capsule())
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func render(_ widget: ModuleWidget) -> some View {
        switch widget.type {
        case "text":
            Text(widget.title.isEmpty ? (widget.configText ?? "") : widget.title)
                .font(.body)
                .foregroundColor(.white)
        case "metric":
            metricCard(widget)
        case "list", "activity_feed":
            listCard(widget)
        case "status", "rich_status":
            statusCard(widget)
        case "markdown":
            Text(widget.configText ?? widget.title).font(.callout)
        case "group", "stack":
            VStack(alignment: .leading, spacing: 8) {
                sectionTitle(widget)
                if widget.title.isEmpty == false {
                    Text(widget.title).foregroundColor(.white)
                }
            }
        default:
            // Unknown / not-yet-native widget types fail safely.
            unsafeWidget(widget)
        }
    }

    private func sectionTitle(_ widget: ModuleWidget) -> some View {
        Text(widget.title.isEmpty ? widget.id : widget.title)
            .font(.headline)
            .foregroundColor(.white)
    }

    private func metricCard(_ widget: ModuleWidget) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(widget.title).font(.caption).foregroundColor(.secondary)
            Text(widget.configText ?? "—")
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundColor(.white)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.joeOSPanel.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func listCard(_ widget: ModuleWidget) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionTitle(widget)
            Divider()
            if let items = widget.configArray, !items.isEmpty {
                ForEach(items, id: \.self) { item in
                    Text(item).font(.callout).foregroundColor(.white)
                }
            } else {
                Text("No items").font(.callout).foregroundColor(.secondary)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.joeOSPanel.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func statusCard(_ widget: ModuleWidget) -> some View {
        HStack(spacing: 8) {
            Circle().fill(Color.joeOSCyan).frame(width: 8, height: 8)
            Text(widget.title).font(.callout).foregroundColor(.white)
            Spacer()
        }
        .padding()
        .background(Color.joeOSPanel.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func unsafeWidget(_ widget: ModuleWidget) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundColor(.yellow)
            Text("Component “\(widget.type)” is not available on this client.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.joeOSPanel.opacity(0.4))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private extension ModuleWidget {
    var configText: String? {
        guard case .string(let value)? = config["text"] ?? config["value"] else { return nil }
        return value
    }

    var configArray: [String]? {
        guard case .array(let values)? = config["items"] else { return nil }
        var result: [String] = []
        for value in values {
            if case .string(let string) = value { result.append(string) }
        }
        return result.isEmpty ? nil : result
    }
}
