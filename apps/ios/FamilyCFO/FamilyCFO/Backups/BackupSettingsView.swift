import SwiftUI
import UIKit

/// Off-box backup to a Synology over SMB (M98). Enter the Synology's address and
/// credentials — the server uploads encrypted backups directly, no mounting. Test
/// the connection, pick a schedule, see status, and restore from the share.
struct BackupSettingsView: View {
    @State var viewModel: BackupViewModel
    @State private var pendingRestore: Components.Schemas.RemoteBackup?
    @State private var pendingLocalRestore: Components.Schemas.BackupJob?

    var body: some View {
        Form {
            connectionSection
            scheduleSection
            statusSection
            if !viewModel.remoteBackups.isEmpty {
                restoreSection
            }
            if !viewModel.localBackups.isEmpty {
                onBoxSection
            }
            keySection
            helpSection
        }
        .navigationTitle("Backups")
        .navigationBarTitleDisplayMode(.inline)
        .keyboardDoneButton()
        .task { await viewModel.load() }
        .overlay {
            if viewModel.isLoading && viewModel.latest == nil { ProgressView() }
        }
        .onChange(of: viewModel.frequency) { Task { await viewModel.save() } }
        // The max-size field has no Return key (decimal pad), so onSubmit never
        // fires — save when its committed value changes instead.
        .onChange(of: viewModel.maxGB) { Task { await viewModel.save() } }
        .alert(
            "Backup", isPresented: .init(
                get: { viewModel.statusMessage != nil },
                set: { if !$0 { viewModel.statusMessage = nil } })
        ) { Button("OK", role: .cancel) {} } message: { Text(viewModel.statusMessage ?? "") }
        .alert(
            "Couldn't complete", isPresented: .init(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } })
        ) { Button("OK", role: .cancel) {} } message: { Text(viewModel.errorMessage ?? "") }
        .confirmationDialog(
            pendingRestore.map { "Restore from \($0.filename)?" } ?? "",
            isPresented: .init(
                get: { pendingRestore != nil }, set: { if !$0 { pendingRestore = nil } }),
            titleVisibility: .visible, presenting: pendingRestore
        ) { backup in
            Button("Restore (replaces all data)", role: .destructive) {
                let target = backup
                pendingRestore = nil
                Task { await viewModel.restore(target) }
            }
            Button("Cancel", role: .cancel) { pendingRestore = nil }
        } message: { _ in
            Text("This overwrites the current database and documents with this backup. It can't be undone.")
        }
        .confirmationDialog(
            "Restore this backup?",
            isPresented: .init(
                get: { pendingLocalRestore != nil },
                set: { if !$0 { pendingLocalRestore = nil } }),
            titleVisibility: .visible, presenting: pendingLocalRestore
        ) { backup in
            Button("Restore (replaces all data)", role: .destructive) {
                let target = backup
                pendingLocalRestore = nil
                Task { await viewModel.restoreLocal(target) }
            }
            Button("Cancel", role: .cancel) { pendingLocalRestore = nil }
        } message: { _ in
            Text("This overwrites the current database and documents with this backup. It can't be undone.")
        }
    }

    private var connectionSection: some View {
        Section {
            field("Synology address", text: $viewModel.host, placeholder: "192.168.1.50", keyboard: .URL)
            field("Shared folder", text: $viewModel.share, placeholder: "family-cfo-backups")
            field("Subfolder (optional)", text: $viewModel.folder, placeholder: "")
            field("Username", text: $viewModel.username, placeholder: "backup-user")
            SecureField("Password", text: $viewModel.password)
                .textContentType(.password)
                .onChange(of: viewModel.password) { viewModel.passwordChanged() }
                .onSubmit { Task { await viewModel.save() } }
            if viewModel.hasStoredPassword && !viewModel.passwordEdited {
                Text("A password is saved. Leave blank to keep it.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button {
                    Task { await viewModel.testConnection() }
                } label: {
                    if viewModel.isChecking { ProgressView() } else { Text("Test connection") }
                }
                .disabled(!viewModel.canTest)
                Spacer()
                if let result = viewModel.checkResult {
                    Label(
                        result.writable ? "Connected" : "Failed",
                        systemImage: result.writable ? "checkmark.circle.fill" : "xmark.circle.fill"
                    )
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(result.writable ? .green : .red)
                }
            }
            if let reason = viewModel.checkResult?.reason {
                Text(reason).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("Synology (SMB)")
        } footer: {
            Text("Backups upload here automatically. Changes save as you go. The password is encrypted on the box and never shown again.")
        }
    }

    private var scheduleSection: some View {
        Section {
            Picker("Back up", selection: $viewModel.frequency) {
                Text("Every 15 min").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.every15min)
                Text("Hourly").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.hourly)
                Text("Every 6 hours").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.every6h)
                Text("Daily").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.daily)
                Text("Weekly").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.weekly)
                Text("Off").tag(Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload.off)
            }
            HStack {
                Text("Max total size")
                Spacer()
                TextField("No limit", value: $viewModel.maxGB, format: .number.precision(.fractionLength(0...1)))
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 90)
                    .onSubmit { Task { await viewModel.save() } }
                Text("GB").foregroundStyle(.secondary)
            }
        } header: {
            Text("Schedule")
        } footer: {
            Text("When all backups combined exceed the limit, the oldest are deleted first. Leave 0 for no limit.")
        }
    }

    private var keySection: some View {
        Section {
            if let key = viewModel.revealedKey {
                Text(key)
                    .font(.footnote.monospaced())
                    .textSelection(.enabled)
                Button {
                    UIPasteboard.general.string = key
                    viewModel.statusMessage = "Encryption key copied."
                } label: {
                    Label("Copy key", systemImage: "doc.on.doc")
                }
            } else {
                Button {
                    Task { await viewModel.revealKey() }
                } label: {
                    Label("Reveal encryption key", systemImage: "key.horizontal")
                }
            }
        } header: {
            Text("Encryption key")
        } footer: {
            Text("This key decrypts every backup — store it somewhere safe (a password manager). Without it, backups can't be restored if the box is lost.")
        }
    }

    private var statusSection: some View {
        Section("Status") {
            if let summary = viewModel.latestSummary {
                LabeledContent("Last backup", value: summary)
            } else {
                Text("No backups yet").foregroundStyle(.secondary)
            }
            if let warning = viewModel.remoteWarning {
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.red)
            } else if viewModel.remoteSynced {
                Label("Copied to Synology", systemImage: "externaldrive.badge.checkmark")
                    .font(.caption).foregroundStyle(.green)
            }
            Button {
                Task { await viewModel.backupNow() }
            } label: {
                if viewModel.isBackingUp {
                    ProgressView()
                } else {
                    Label("Back up now", systemImage: "arrow.clockwise")
                }
            }
            .disabled(viewModel.isBackingUp)
        }
    }

    private var restoreSection: some View {
        Section {
            ForEach(viewModel.remoteBackups, id: \.filename) { backup in
                Button {
                    pendingRestore = backup
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Self.dateLabel(backup.modifiedAt)).foregroundStyle(.primary)
                            Text(ByteCountFormatter.string(fromByteCount: backup.sizeBytes, countStyle: .file))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if viewModel.isRestoring {
                            ProgressView()
                        } else {
                            Image(systemName: "arrow.counterclockwise.circle").foregroundStyle(.orange)
                        }
                    }
                }
                .disabled(viewModel.isRestoring)
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteRemote(backup) }
                    } label: { Label("Delete", systemImage: "trash") }
                }
            }
        } header: {
            Text("Restore from Synology")
        } footer: {
            Text("Backups found on the share, newest first. Restoring replaces everything currently in the app.")
        }
    }

    private var onBoxSection: some View {
        Section {
            ForEach(viewModel.localBackups, id: \.id) { backup in
                Button {
                    pendingLocalRestore = backup
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(backup.completedAt?.formatted(date: .abbreviated, time: .shortened)
                                ?? backup.createdAt.formatted(date: .abbreviated, time: .shortened))
                                .foregroundStyle(.primary)
                            if let size = backup.sizeBytes {
                                Text(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        if viewModel.isRestoring {
                            ProgressView()
                        } else {
                            Image(systemName: "arrow.counterclockwise.circle").foregroundStyle(.orange)
                        }
                    }
                }
                .disabled(viewModel.isRestoring)
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteLocal(backup) }
                    } label: { Label("Delete", systemImage: "trash") }
                }
            }
        } header: {
            Text("On this box")
        } footer: {
            Text("Encrypted backups kept on the box (the last 7). Restoring replaces everything currently in the app.")
        }
    }

    private var helpSection: some View {
        Section {
            DisclosureGroup("Set up the Synology (one time)") {
                VStack(alignment: .leading, spacing: 10) {
                    step(1, "Control Panel → File Services → SMB → enable it.")
                    step(2, "Control Panel → Shared Folder → create one, e.g. “family-cfo-backups”.")
                    step(3, "Control Panel → User → give a user (or a dedicated backup user) read/write on that folder.")
                    step(4, "Back here: enter the Synology's IP, that folder name, the username and password, then tap Test connection.")
                }
                .font(.caption).padding(.vertical, 4)
            }
        } footer: {
            Text("Keep your backup encryption key safe — it's required to restore, even from the Synology.")
        }
    }

    private func field(
        _ title: String, text: Binding<String>, placeholder: String,
        keyboard: UIKeyboardType = .default
    ) -> some View {
        HStack {
            Text(title)
            Spacer()
            TextField(placeholder, text: text)
                .multilineTextAlignment(.trailing)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .keyboardType(keyboard)
                .onSubmit { Task { await viewModel.save() } }
        }
    }

    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text("\(n).").font(.caption.weight(.bold)).foregroundStyle(.secondary)
            Text(text).textSelection(.enabled)
        }
    }

    static func dateLabel(_ epoch: Int64) -> String {
        Date(timeIntervalSince1970: TimeInterval(epoch))
            .formatted(date: .abbreviated, time: .shortened)
    }
}
