import JoeOSCore
import SwiftUI

struct ConnectionSettingsView: View {
    @Environment(\.dismiss) private var dismiss

    @Binding private var encodedProfiles: String
    @Binding private var activeProfileID: String

    @State private var profiles: [ConnectionProfile]
    @State private var selectedProfileID: UUID
    @State private var activeDraftID: UUID
    @State private var persistenceError: String?

    init(encodedProfiles: Binding<String>, activeProfileID: Binding<String>) {
        _encodedProfiles = encodedProfiles
        _activeProfileID = activeProfileID

        let decoded = ConnectionProfileStorage.decode(encodedProfiles.wrappedValue)
        let storedActiveID = UUID(uuidString: activeProfileID.wrappedValue)
        let active = decoded.first(where: { $0.id == storedActiveID }) ?? decoded[0]
        _profiles = State(initialValue: decoded)
        _selectedProfileID = State(initialValue: active.id)
        _activeDraftID = State(initialValue: active.id)
    }

    var body: some View {
        NavigationStack {
            Form {
                profileList
                profileEditor
                transportPolicy
                privacySection
            }
            .scrollContentBackground(.hidden)
            .background(Color.joeOSCanvas)
            .navigationTitle("Connections")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color.joeOSPanel, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: save)
                        .fontWeight(.semibold)
                        .disabled(!canSave)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var profileList: some View {
        Section {
            ForEach(profiles) { profile in
                Button {
                    selectedProfileID = profile.id
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: activeDraftID == profile.id ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(activeDraftID == profile.id ? Color.joeOSCyan : .secondary)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(profile.name.isEmpty ? "Unnamed connection" : profile.name)
                                .foregroundStyle(.primary)
                            Text(profile.endpoint)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        if selectedProfileID == profile.id {
                            Image(systemName: "slider.horizontal.3")
                                .foregroundStyle(Color.joeOSCyan)
                                .accessibilityHidden(true)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Edit \(profile.name) connection")
                .swipeActions(edge: .trailing, allowsFullSwipe: profiles.count > 1) {
                    Button(role: .destructive) {
                        delete(profile.id)
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                    .disabled(profiles.count == 1)
                }
            }

            Button(action: addProfile) {
                Label("Add connection", systemImage: "plus.circle.fill")
                    .foregroundStyle(Color.joeOSCyan)
            }
        } header: {
            Text("Connection profiles")
        } footer: {
            Text("The checked profile opens when JoeOS Client starts.")
        }
    }

    @ViewBuilder
    private var profileEditor: some View {
        if let index = selectedIndex {
            Section("Selected profile") {
                TextField("Name", text: profileBinding(\.name, at: index))
                    .textInputAutocapitalization(.words)
                    .autocorrectionDisabled()

                TextField("JoeOS address", text: profileBinding(\.endpoint, at: index))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.body.monospaced())

                if let validationMessage = validationMessage(for: profiles[index]) {
                    Label(validationMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                } else {
                    Label("Address passes the JoeOS transport policy.", systemImage: "checkmark.shield.fill")
                        .font(.footnote)
                        .foregroundStyle(Color.joeOSGreen)
                }

                Button {
                    activeDraftID = profiles[index].id
                } label: {
                    Label(
                        activeDraftID == profiles[index].id ? "Active connection" : "Use this connection",
                        systemImage: activeDraftID == profiles[index].id ? "checkmark.circle.fill" : "arrow.right.circle"
                    )
                }
                .disabled(activeDraftID == profiles[index].id || validationMessage(for: profiles[index]) != nil)
            }
        }
    }

    private var transportPolicy: some View {
        Section("Transport policy") {
            Label("HTTPS is allowed for any valid host.", systemImage: "lock.fill")
            Label("HTTP is limited to loopback, private, link-local, .local, and Tailscale 100.64/10 addresses.", systemImage: "network")
            Text("Use Tailscale Serve or another private HTTPS reverse proxy for production access. Public HTTP is always rejected before WebKit loads it.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private var privacySection: some View {
        Section("Privacy & storage") {
            Text("Profile names and server addresses are non-secret preferences stored on this iPhone with AppStorage. JoeOS Client does not store passwords, API keys, approval tokens, or model credentials.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            Text("The client connects only to the selected JoeOS origin. It never connects directly to Lemonade, Ollama, a shell, or an MCP runner.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            if let persistenceError {
                Label(persistenceError, systemImage: "exclamationmark.circle.fill")
                    .font(.footnote)
                    .foregroundStyle(.red)
            }

            Button("Restore default Halo", role: .destructive, action: restoreDefault)
        }
    }

    private var selectedIndex: Int? {
        profiles.firstIndex(where: { $0.id == selectedProfileID })
    }

    private var canSave: Bool {
        guard !profiles.isEmpty,
              profiles.contains(where: { $0.id == activeDraftID })
        else {
            return false
        }

        return profiles.allSatisfy { profile in
            !profile.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            validationMessage(for: profile) == nil
        }
    }

    private func profileBinding<Value>(
        _ keyPath: WritableKeyPath<ConnectionProfile, Value>,
        at index: Int
    ) -> Binding<Value> {
        Binding(
            get: { profiles[index][keyPath: keyPath] },
            set: { profiles[index][keyPath: keyPath] = $0 }
        )
    }

    private func validationMessage(for profile: ConnectionProfile) -> String? {
        guard !profile.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return "Give this connection a name."
        }
        guard case .failure(let error) = EndpointPolicy.validate(profile.endpoint) else {
            return nil
        }
        return error.localizedDescription
    }

    private func addProfile() {
        let profile = ConnectionProfile(
            name: "Halo \(profiles.count + 1)",
            endpoint: ConnectionProfile.defaultHalo.endpoint
        )
        profiles.append(profile)
        selectedProfileID = profile.id
    }

    private func delete(_ id: UUID) {
        guard profiles.count > 1,
              let index = profiles.firstIndex(where: { $0.id == id })
        else {
            return
        }

        profiles.remove(at: index)
        if activeDraftID == id {
            activeDraftID = profiles[0].id
        }
        if selectedProfileID == id {
            selectedProfileID = profiles[0].id
        }
    }

    private func restoreDefault() {
        profiles = [.defaultHalo]
        selectedProfileID = ConnectionProfile.defaultHalo.id
        activeDraftID = ConnectionProfile.defaultHalo.id
        persistenceError = nil
    }

    private func save() {
        guard canSave else { return }
        do {
            let normalized = try profiles.map { profile -> ConnectionProfile in
                let endpoint = try EndpointPolicy.validate(profile.endpoint).get().url.absoluteString
                return ConnectionProfile(
                    id: profile.id,
                    name: profile.name.trimmingCharacters(in: .whitespacesAndNewlines),
                    endpoint: endpoint
                )
            }
            encodedProfiles = try ConnectionProfileStorage.encode(normalized)
            activeProfileID = activeDraftID.uuidString
            dismiss()
        } catch {
            persistenceError = "The connection preferences could not be saved."
        }
    }
}
