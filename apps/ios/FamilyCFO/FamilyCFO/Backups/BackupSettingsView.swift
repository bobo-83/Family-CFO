import SwiftUI
import UIKit

/// Off-box backup to a Synology over SMB (M98). Enter the Synology's address and
/// credentials — the server uploads encrypted backups directly, no mounting. Test
/// the connection, pick a schedule, see status, and restore from the share.
struct BackupSettingsView: View {
    @State var viewModel: BackupViewModel
    @State private var expandedDays: Set<String> = []
    @State private var showingExportShare = false
    @State private var pendingRestore: Components.Schemas.RemoteBackup?
    @State private var pendingLocalRestore: Components.Schemas.BackupJob?
    @State private var confirmReplaceRecoveryKey = false
    /// ADR 0072 Phase 3: the armed privacy-mode switch — true targets sealed,
    /// false targets convenient. Nil = no confirmation showing.
    @State private var pendingSealTarget: Bool?
    /// "Unlock with recovery key…" — the rescue for a locked household. The
    /// entered key lives only in this state: never logged, never persisted.
    @State private var showRecoveryUnlock = false
    @State private var recoveryUnlockKey = ""

    var body: some View {
        Form {
            connectionSection
            scheduleSection
            retentionSection
            statusSection
            recoverySection
            if !viewModel.remoteBackups.isEmpty || viewModel.remoteListError != nil {
                restoreSection
            }
            if !viewModel.localBackups.isEmpty || viewModel.localListError != nil {
                onBoxSection
            }
            restoreKeysSection
            exportSection
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
        .alert(
            pendingSealTarget == true ? "Seal this household?" : "Switch back to convenient?",
            isPresented: .init(
                get: { pendingSealTarget != nil },
                set: { if !$0 { pendingSealTarget = nil } }),
            presenting: pendingSealTarget
        ) { sealed in
            Button(sealed ? "Seal" : "Switch to convenient") {
                pendingSealTarget = nil
                Task { await viewModel.setSealMode(sealed: sealed) }
            }
            Button("Cancel", role: .cancel) { pendingSealTarget = nil }
        } message: { sealed in
            // One sentence restating the consequence (ADR 0072 Phase 3).
            Text(
                sealed
                    ? "After a restart, nothing is readable until someone signs in. Unattended sync, snapshots and study then run while the in-memory key session is open. It expires 30 minutes after its last member-driven use; signing out does not close it immediately."
                    : "The box keeps a spare of your data key again, so overnight work runs without anyone signed in."
            )
        }
        .alert("Replace recovery key?", isPresented: $confirmReplaceRecoveryKey) {
            Button("Replace", role: .destructive) {
                Task { await viewModel.createRecoveryKey() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The old recovery key stops working immediately.")
        }
        .alert(
            "Restore this backup?",
            isPresented: .init(
                get: { pendingRestore != nil }, set: { if !$0 { pendingRestore = nil } }),
            presenting: pendingRestore
        ) { backup in
            Button("Restore (replaces all data)", role: .destructive) {
                let target = backup
                pendingRestore = nil
                Task { await viewModel.restore(target) }
            }
            Button("Cancel", role: .cancel) { pendingRestore = nil }
        } message: { backup in
            Text(
                "This overwrites the current database and documents with the selected Synology file timestamped \(Self.dayTimeLabel(backup.modifiedAt)). The share's file time can reflect upload time rather than the exact snapshot time. It can't be undone."
            )
        }
        .alert(
            "Restore this backup?",
            isPresented: .init(
                get: { pendingLocalRestore != nil },
                set: { if !$0 { pendingLocalRestore = nil } }),
            presenting: pendingLocalRestore
        ) { backup in
            Button("Restore (replaces all data)", role: .destructive) {
                let target = backup
                pendingLocalRestore = nil
                Task { await viewModel.restoreLocal(target) }
            }
            Button("Cancel", role: .cancel) { pendingLocalRestore = nil }
        } message: { backup in
            Text(
                "This overwrites the current database and documents with the backup from \((backup.completedAt ?? backup.createdAt).formatted(date: .abbreviated, time: .shortened)). It can't be undone."
            )
        }
    }

    private var connectionSection: some View {
        Section {
            field(
                String(localized: "Synology address"), text: $viewModel.host,
                placeholder: "192.168.1.50", keyboard: .URL)
            field(
                String(localized: "Shared folder"), text: $viewModel.share,
                placeholder: "family-cfo-backups")
            field(
                String(localized: "Subfolder (optional)"), text: $viewModel.folder, placeholder: "")
            field(
                String(localized: "Username"), text: $viewModel.username,
                placeholder: "backup-user")
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
                // The server's own words for why the check failed.
                Text(verbatim: reason).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("Synology (SMB)")
        } footer: {
            Text("Destination and schedule changes save as you go. Retention changes use the separate activation button below. The password is encrypted on the box and never shown again.")
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
        } header: {
            Text("Schedule")
        } footer: {
            Text("The schedule is box-global. Turning it off stops scheduled creation, but maintenance and manual backups remain available.")
        }
    }

    private var retentionSection: some View {
        Section {
            retentionEditor(
                title: String(localized: "On this box"),
                systemImage: "internaldrive",
                draft: $viewModel.localRetention)
            Divider()
            retentionEditor(
                title: String(localized: "Synology"),
                systemImage: "externaldrive",
                draft: $viewModel.offboxRetention)

            if viewModel.retentionReviewRequired {
                Label(
                    "Automatic pruning is paused until a system administrator reviews and activates this policy.",
                    systemImage: "pause.circle.fill"
                )
                .font(.caption)
                .foregroundStyle(.orange)
                .accessibilityAddTraits(.isStaticText)
            }
            if viewModel.legacyConflictDetected {
                Label(
                    "Older household settings differed. Review the box-global policy before activation.",
                    systemImage: "exclamationmark.triangle.fill"
                )
                .font(.caption)
                .foregroundStyle(.orange)
            }
            pendingPrunePreview(
                title: String(localized: "On this box"),
                count: viewModel.localPendingPruneCount,
                bytes: viewModel.localPendingPruneBytes)
            pendingPrunePreview(
                title: String(localized: "Synology"),
                count: viewModel.offboxPendingPruneCount,
                bytes: viewModel.offboxPendingPruneBytes)

            if let validation = viewModel.retentionValidationMessage {
                Label(validation, systemImage: "exclamationmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .accessibilityLabel(String(localized: "Retention validation error: \(validation)"))
            }
            if let error = viewModel.configError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            if let current = viewModel.conflictingConfig {
                VStack(alignment: .leading, spacing: 6) {
                    Text("The box has newer settings. Your unsaved draft is preserved.")
                        .font(.caption)
                    Text("Current box revision: \(current.updatedAt.formatted(date: .abbreviated, time: .shortened))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Button("Use current box settings") {
                        Task { await viewModel.useCurrentBoxSettings() }
                    }
                }
            }

            Button {
                Task { await viewModel.saveAndActivateRetention() }
            } label: {
                if viewModel.isActivatingRetention {
                    HStack { ProgressView(); Text("Saving and activating…") }
                } else {
                    Label("Save and activate retention", systemImage: "checkmark.shield")
                }
            }
            .disabled(!viewModel.canActivateRetention)
            .accessibilityHint("Confirms that automatic policy and capacity pruning may run during the next maintenance pass.")
        } header: {
            Text("Retention and capacity")
        } footer: {
            Text("Policy, maximum size, and reserved free space stay as a draft until you activate them. Activation does not delete during this request; the next locked maintenance pass applies the policy.")
        }
    }

    private func retentionEditor(
        title: String, systemImage: String, draft: Binding<BackupRetentionDraft>
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            Picker("Retention mode", selection: draft.mode) {
                Text("Tiered").tag(Components.Schemas.BackupRetentionPolicyUpdate.ModePayload.tiered)
                Text("Keep every backup").tag(Components.Schemas.BackupRetentionPolicyUpdate.ModePayload.keepAll)
            }
            if draft.wrappedValue.mode == .tiered {
                retentionDaysField("Keep every backup for", value: draft.keepAllDays)
                retentionDaysField("Keep one per day through", value: draft.dailyUntilDays)
                retentionDaysField("Keep one per week through", value: draft.weeklyUntilDays)
            }
            retentionGBField("Maximum total size", value: draft.maxGB, zeroMeansUnlimited: true)
            retentionGBField("Keep at least this much space free", value: draft.reserveGB)
            Text(verbatim: BackupViewModel.retentionSummary(draft.wrappedValue))
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityLabel("\(title) policy: \(BackupViewModel.retentionSummary(draft.wrappedValue))")
        }
    }

    private func retentionDaysField(_ title: LocalizedStringKey, value: Binding<Int>) -> some View {
        LabeledContent(title) {
            HStack(spacing: 4) {
                TextField("Days", value: value, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 90)
                Text("days").foregroundStyle(.secondary)
            }
        }
    }

    private func retentionGBField(
        _ title: LocalizedStringKey, value: Binding<Double>, zeroMeansUnlimited: Bool = false
    ) -> some View {
        LabeledContent(title) {
            HStack(spacing: 4) {
                TextField(
                    zeroMeansUnlimited ? "No limit" : "GB",
                    value: value,
                    format: .number.precision(.fractionLength(0...2)))
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 90)
                Text(verbatim: "GB").foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private func pendingPrunePreview(title: String, count: Int?, bytes: Int64?) -> some View {
        if let count, count > 0 {
            let size = bytes.map {
                ByteCountFormatter.string(fromByteCount: $0, countStyle: .file)
            } ?? String(localized: "size unknown")
            Label(
                "\(title) pending after activation: \(count) backups · \(size)",
                systemImage: "clock.badge.exclamationmark"
            )
            .font(.caption)
            .foregroundStyle(.orange)
        }
    }

    private var recoverySection: some View {
        Section {
            if let status = viewModel.recoveryStatus {
                recoveryDestination(status.local, title: String(localized: "On this box"))
                Divider()
                recoveryDestination(status.offbox, title: String(localized: "Synology"))
                Text("Observed \(status.asOf.formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else if let error = viewModel.recoveryStatusError {
                Label("Recovery status unavailable", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .accessibilityAddTraits(.isStaticText)
                Text(verbatim: error)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack { ProgressView(); Text("Loading recovery status…") }
            }
        } header: {
            Text("Recovery window")
        } footer: {
            Text("These are visible, read-probed recovery candidates—not guaranteed restore points. Archive integrity, the backup key, household recovery key, decryption, migrations, and a complete database restore are checked only during restore.")
        }
    }

    private func recoveryDestination(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus, title: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            let stateTitle = BackupViewModel.destinationStateTitle(status.status)
            Label("\(title): \(stateTitle)", systemImage: recoverySymbol(status.status))
                .font(.headline)
                .foregroundStyle(recoveryColor(status.status))
                .accessibilityLabel("\(title) recovery state: \(stateTitle)")

            LabeledContent("Configured policy", value: BackupViewModel.policySummary(status.policy))
            if let target = status.policy.targetOldestAt {
                LabeledContent("Configured target oldest date") {
                    Text(target.formatted(date: .abbreviated, time: .shortened))
                        .accessibilityLabel("\(title) configured target oldest date, \(target.formatted(date: .complete, time: .shortened))")
                }
            } else if status.policy.mode == .keepAll {
                LabeledContent("Configured target", value: String(localized: "Keep every backup"))
            }

            if let oldest = status.oldestReadableAt {
                LabeledContent("Oldest readable backup currently visible") {
                    Text(oldest.formatted(date: .abbreviated, time: .shortened))
                        .accessibilityLabel("\(title) oldest readable backup currently visible, \(oldest.formatted(date: .complete, time: .shortened))")
                }
            }
            if let newest = status.newestReadableAt {
                LabeledContent("Newest readable backup currently visible") {
                    Text(newest.formatted(date: .abbreviated, time: .shortened))
                        .accessibilityLabel("\(title) newest readable backup currently visible, \(newest.formatted(date: .complete, time: .shortened))")
                }
            }

            LabeledContent("Visible managed backups", value: "\(status.visibleArchiveCount)")
            LabeledContent(
                "Readable backups",
                value: status.readableArchiveCount.map(String.init) ?? String(localized: "Not fully known"))
            LabeledContent(
                "Read probe",
                value: "\(BackupViewModel.probeTitle(status.probeStatus)) · \(status.probedArchiveCount) probed")
            LabeledContent("Coverage", value: BackupViewModel.coverageTitle(status.coverageStatus))
            LabeledContent("Capacity", value: BackupViewModel.capacityTitle(status.capacity.status))
            Text(verbatim: BackupViewModel.capacityMessage(status.capacity))
                .font(.caption)
                .foregroundStyle(.secondary)

            if status.oldestTimestampSource == .remoteModifiedAt {
                Label(
                    "The oldest Synology candidate uses the remote file's modified time because matching job metadata is unavailable.",
                    systemImage: "clock.arrow.circlepath"
                )
                .font(.caption)
                .foregroundStyle(.orange)
            }
            if status.metadataMismatchCount > 0 || status.protectedAnomalyCount > 0
                || status.compatibilityUnknownCount > 0 || status.knownIncompatibleCount > 0
            {
                Label(
                    "Metadata mismatches: \(status.metadataMismatchCount) · protected anomalies: \(status.protectedAnomalyCount) · compatibility unknown: \(status.compatibilityUnknownCount) · incompatible: \(status.knownIncompatibleCount)",
                    systemImage: "exclamationmark.triangle.fill"
                )
                .font(.caption)
                .foregroundStyle(.orange)
            }
            if let message = BackupViewModel.destinationStateMessage(status) {
                Label(message, systemImage: status.status == .healthy ? "checkmark.circle" : "info.circle")
                    .font(.caption)
                    .foregroundStyle(status.status == .unavailable || status.status == .constrained ? .orange : .secondary)
            }
            if let reason = status.reason {
                Text(verbatim: reason).font(.caption).foregroundStyle(.secondary)
            }
            Text("Candidate observation: \(status.asOf.formatted(date: .abbreviated, time: .shortened))")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .accessibilityElement(children: .contain)
    }

    private func recoverySymbol(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus.StatusPayload
    ) -> String {
        switch status {
        case .healthy: return "checkmark.circle.fill"
        case .empty, .notConfigured: return "minus.circle"
        case .constrained: return "externaldrive.badge.exclamationmark"
        case .degraded: return "exclamationmark.triangle.fill"
        case .unavailable: return "wifi.exclamationmark"
        }
    }

    private func recoveryColor(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus.StatusPayload
    ) -> Color {
        switch status {
        case .healthy: return .green
        case .empty, .notConfigured: return .secondary
        case .constrained, .degraded, .unavailable: return .orange
        }
    }

    /// Both restore keys as numbered steps in ONE section — user feedback said
    /// "Encryption key" / "Data encryption" didn't convey what each is for.
    /// 1 · Backup key opens the archives; 2 · Recovery key (ADR 0072 Phase 2)
    /// unlocks the per-household content inside. The recovery key is shown
    /// ONCE, right after minting, and can never be retrieved again.
    private var restoreKeysSection: some View {
        Section {
            stepTitle(
                String(localized: "1 · Backup key"),
                subtitle: String(localized: "Opens your backup files."))
            if let key = viewModel.revealedKey {
                Text(verbatim: key)
                    .font(.footnote.monospaced())
                    .textSelection(.enabled)
                Button {
                    UIPasteboard.general.string = key
                    viewModel.statusMessage = String(localized: "Backup key copied.")
                } label: {
                    Label("Copy key", systemImage: "doc.on.doc")
                }
            } else {
                Button {
                    Task { await viewModel.revealKey() }
                } label: {
                    Label("Reveal backup key", systemImage: "key.horizontal")
                }
            }
            if let status = viewModel.keyStatus {
                if !status.encryptionEnabled {
                    stepTitle(
                        String(localized: "2 · Recovery key"),
                        subtitle: String(localized: "Per-household encryption is off on this box."))
                } else {
                    VStack(alignment: .leading, spacing: 4) {
                        stepTitle(
                            String(localized: "2 · Recovery key"),
                            subtitle: String(
                                localized:
                                    "Unlocks the content inside — the spare for your passwords and phones."
                            )
                        )
                        Text(
                            "Content encrypted per household · \(status.memberWraps) member keys, \(status.deviceWraps) device keys"
                        )
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    }
                    if let key = viewModel.generatedRecoveryKey {
                        Label(
                            "This is the only time it will be shown. Store it somewhere safe — it is one of the keys that can unlock your household's data.",
                            systemImage: "exclamationmark.triangle.fill"
                        )
                        .font(.caption)
                        .foregroundStyle(.orange)
                        Text(verbatim: key)
                            .font(.footnote.monospaced())
                            .textSelection(.enabled)
                        Button {
                            UIPasteboard.general.string = key
                            viewModel.statusMessage = String(localized: "Recovery key copied.")
                        } label: {
                            Label("Copy recovery key", systemImage: "doc.on.doc")
                        }
                    } else if status.hasRecoveryKey {
                        if let created = status.recoveryKeyCreatedAt {
                            Text("Recovery key created \(created.formatted(date: .abbreviated, time: .omitted)).")
                        } else {
                            Text("Recovery key created.")
                        }
                        Button("Replace recovery key…") {
                            confirmReplaceRecoveryKey = true
                        }
                    } else {
                        Label(
                            "No recovery key yet. Without one, losing every password and paired phone loses the data. Create it and store it beside your backup key.",
                            systemImage: "exclamationmark.triangle.fill"
                        )
                        .font(.caption)
                        .foregroundStyle(.orange)
                        Button("Create recovery key") {
                            Task { await viewModel.createRecoveryKey() }
                        }
                    }
                    if let mode = status.mode {
                        privacyModeRows(mode: mode, unlocked: status.unlocked ?? true)
                    } else if status.unlocked == false {
                        // No mode reported but still locked (encryption on) —
                        // the same rescue applies.
                        recoveryUnlockRows
                    }
                }
            }
        } header: {
            Text("Restore keys")
        } footer: {
            Text("Restoring onto a new box takes both keys — store them together in a password manager.")
        }
    }

    /// ADR 0072 Phase 3: the household's privacy mode, below the recovery-key
    /// step. Each mode states only its own claim — never more (ADR 0070).
    @ViewBuilder private func privacyModeRows(
        mode: Components.Schemas.HouseholdKeyStatus.ModePayload, unlocked: Bool
    ) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Privacy mode").font(.subheadline.weight(.semibold))
            Text(mode == .sealed ? "Sealed" : "Convenient")
                .font(.caption.weight(.semibold))
            Text(
                mode == .sealed
                    ? "Only your passwords, your phones, and your recovery key can open your data. After a restart, nothing is readable until someone signs in. Unattended work catches up while the in-memory key session is open. It expires 30 minutes after its last member-driven use; signing out does not close it immediately. Encrypted backups keep running either way."
                    : "The box keeps a spare of your data key: overnight sync, snapshots, and idle study keep working. Your content is protected against stolen disks and backups — the box itself can still read it."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            if mode == .sealed && !unlocked {
                Text("Locked — sign in again to unlock")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        if !unlocked {
            // Locked in either mode (sealed after a restart, or convenient
            // restored without its master key) — the recovery key is the rescue.
            recoveryUnlockRows
        }
        Button(mode == .sealed ? "Switch back to convenient…" : "Seal this household…") {
            pendingSealTarget = (mode != .sealed)
        }
    }

    /// "Unlock with recovery key…" beneath the locked line: tapping reveals an
    /// inline SecureField (same pattern as the SMB password row). A 400 keeps
    /// the field open — the server's message shows in the error alert.
    @ViewBuilder private var recoveryUnlockRows: some View {
        if showRecoveryUnlock {
            SecureField("FCFO-…", text: $recoveryUnlockKey)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .onSubmit { Task { await submitRecoveryUnlock() } }
            Button("Unlock") {
                Task { await submitRecoveryUnlock() }
            }
            .disabled(recoveryUnlockKey.trimmingCharacters(in: .whitespaces).isEmpty)
        } else {
            Button("Unlock with recovery key…") { showRecoveryUnlock = true }
        }
    }

    private func submitRecoveryUnlock() async {
        await viewModel.unlockWithRecoveryKey(recoveryUnlockKey)
        // Only a real unlock clears the field — a wrong key keeps it open for
        // another try (the alert already showed the server's message).
        if viewModel.keyStatus?.unlocked == true {
            recoveryUnlockKey = ""
            showRecoveryUnlock = false
        }
    }

    /// A numbered step heading + its one-line purpose, as a single Form row.
    /// Both strings are already localized by the caller (`String(localized:)`).
    private func stepTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline.weight(.semibold))
            Text(subtitle).font(.caption).foregroundStyle(.secondary)
        }
    }

    /// "Export my data" (#189): the whole household as a portable zip. ONE tap —
    /// the share sheet presents itself as soon as the file is ready, rather
    /// than making the user notice and tap a second row. A 423 (sealed
    /// household, locked) surfaces through the error alert, verbatim.
    private var exportSection: some View {
        Section {
            Button {
                Task {
                    await viewModel.exportData()
                    if viewModel.exportedFileURL != nil {
                        showingExportShare = true
                    }
                }
            } label: {
                if viewModel.isExporting {
                    HStack { ProgressView(); Text("Preparing export…").padding(.leading, 8) }
                } else {
                    Label("Export my data", systemImage: "square.and.arrow.down")
                }
            }
            .disabled(viewModel.isExporting)
            .sheet(isPresented: $showingExportShare) {
                if let url = viewModel.exportedFileURL {
                    ShareSheet(items: [url])
                }
            }
        } header: {
            Text("Your data")
        } footer: {
            Text(
                "Download everything in this household — accounts, transactions, advisor history, and documents — as a zip you can keep or take elsewhere."
            )
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

    /// One row per DAY, newest first, today expanded — four snapshots a day
    /// made the flat list an endless scroll (user report 2026-07-26).
    private var restoreSection: some View {
        Section {
            if let error = viewModel.remoteListError {
                Label("Synology backup list unavailable", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text(verbatim: error)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            ForEach(groupedRemoteBackups, id: \.day) { group in
                DisclosureGroup(
                    isExpanded: Binding(
                        get: { expandedDays.contains(group.day) },
                        set: { open in
                            if open { expandedDays.insert(group.day) } else {
                                expandedDays.remove(group.day)
                            }
                        }
                    )
                ) {
                    ForEach(group.backups, id: \.filename) { backup in
                        remoteBackupRow(backup)
                    }
                } label: {
                    HStack {
                        Text(verbatim: group.day)
                        Spacer()
                        Text("\(group.backups.count) backups")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        } header: {
            Text("Restore from Synology")
        } footer: {
            Text("Grouped by the Synology file time, newest first — tap a day for its files. That time can reflect upload time rather than the exact snapshot time. Restoring replaces everything currently in the app.")
        }
        .onAppear {
            if expandedDays.isEmpty, let newest = groupedRemoteBackups.first {
                expandedDays.insert(newest.day)
            }
        }
    }

    private var groupedRemoteBackups: [(day: String, backups: [Components.Schemas.RemoteBackup])] {
        let calendar = Calendar.current
        let grouped = Dictionary(grouping: viewModel.remoteBackups) { backup in
            calendar.startOfDay(for: Date(timeIntervalSince1970: TimeInterval(backup.modifiedAt)))
        }
        return grouped.keys.sorted(by: >).map { day in
            (
                day: day.formatted(date: .abbreviated, time: .omitted),
                backups: grouped[day]!.sorted { $0.modifiedAt > $1.modifiedAt }
            )
        }
    }

    private func remoteBackupRow(_ backup: Components.Schemas.RemoteBackup) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: Self.timeLabel(backup.modifiedAt)).foregroundStyle(.primary)
                Text(
                    verbatim: ByteCountFormatter.string(
                        fromByteCount: backup.sizeBytes, countStyle: .file)
                )
                .font(.caption).foregroundStyle(.secondary)
                versionLabel(backup.appVersion)
            }
            Spacer()
            if viewModel.isRestoring {
                ProgressView()
            } else {
                // Restore arms only from this button — a tap on the row's
                // description must never raise the destructive confirmation.
                Button {
                    pendingRestore = backup
                } label: {
                    Image(systemName: "arrow.counterclockwise.circle").foregroundStyle(.orange)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(
                    String(localized: "Restore Synology backup file timestamped \(Self.dayTimeLabel(backup.modifiedAt))"))
            }
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await viewModel.deleteRemote(backup) }
            } label: { Label("Delete", systemImage: "trash") }
        }
    }

    static func timeLabel(_ epoch: Int64) -> String {
        Date(timeIntervalSince1970: TimeInterval(epoch))
            .formatted(date: .omitted, time: .shortened)
    }

    /// Day + time, for the restore alert — the centered alert isn't anchored
    /// to a row, so it must say which backup it is about.
    static func dayTimeLabel(_ epoch: Int64) -> String {
        Date(timeIntervalSince1970: TimeInterval(epoch))
            .formatted(date: .abbreviated, time: .shortened)
    }

    /// The app version that made a backup. Nil (pre-versioning) stays subtle;
    /// a version NEWER than the box gets the warning treatment — the server
    /// refuses that restore (409) until the box updates.
    @ViewBuilder private func versionLabel(_ appVersion: String?) -> some View {
        if let appVersion {
            if viewModel.isFromNewerVersion(appVersion) {
                Text("v\(appVersion) — made by a newer version, update first")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.orange)
            } else {
                Text(verbatim: "v\(appVersion)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        } else {
            Text("version unknown")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var onBoxSection: some View {
        Section {
            if let error = viewModel.localListError {
                Label("On-box backup list unavailable", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text(verbatim: error)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            ForEach(viewModel.localBackups, id: \.id) { backup in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(
                            verbatim: backup.completedAt?.formatted(
                                date: .abbreviated, time: .shortened)
                                ?? backup.createdAt.formatted(
                                    date: .abbreviated, time: .shortened)
                        )
                        .foregroundStyle(.primary)
                        if let size = backup.sizeBytes {
                            Text(
                                verbatim: ByteCountFormatter.string(
                                    fromByteCount: size, countStyle: .file)
                            )
                            .font(.caption).foregroundStyle(.secondary)
                        }
                        versionLabel(backup.appVersion)
                    }
                    Spacer()
                    if viewModel.isRestoring {
                        ProgressView()
                    } else {
                        // Same rule as the Synology rows: only the button arms
                        // the destructive confirmation, never the row itself.
                        Button {
                            pendingLocalRestore = backup
                        } label: {
                            Image(systemName: "arrow.counterclockwise.circle").foregroundStyle(.orange)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel(
                            String(
                                localized: "Restore on-box backup from \((backup.completedAt ?? backup.createdAt).formatted(date: .complete, time: .shortened))"))
                    }
                }
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteLocal(backup) }
                    } label: { Label("Delete", systemImage: "trash") }
                }
            }
        } header: {
            Text("On this box")
        } footer: {
            Text("Encrypted backups kept on the box. See Retention and capacity for the configured policy and Recovery window for currently observed candidates. Restoring replaces everything currently in the app.")
        }
    }

    private var helpSection: some View {
        Section {
            DisclosureGroup("Set up the Synology (one time)") {
                VStack(alignment: .leading, spacing: 10) {
                    step(1, String(localized: "Control Panel → File Services → SMB → enable it."))
                    step(
                        2,
                        String(
                            localized:
                                "Control Panel → Shared Folder → create one, e.g. “family-cfo-backups”."
                        ))
                    step(
                        3,
                        String(
                            localized:
                                "Control Panel → User → give a user (or a dedicated backup user) read/write on that folder."
                        ))
                    step(
                        4,
                        String(
                            localized:
                                "Back here: enter the Synology's IP, that folder name, the username and password, then tap Test connection."
                        ))
                }
                .font(.caption).padding(.vertical, 4)
            }
        } footer: {
            Text("Keep your backup key safe — it's required to restore, even from the Synology.")
        }
    }

    /// `title` is already localized by the caller; `placeholder` is a literal
    /// example (an address, a share name) that deliberately stays as typed.
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

    /// `text` is already localized by the caller (`String(localized:)`).
    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(verbatim: "\(n).").font(.caption.weight(.bold)).foregroundStyle(.secondary)
            Text(text).textSelection(.enabled)
        }
    }

    static func dateLabel(_ epoch: Int64) -> String {
        Date(timeIntervalSince1970: TimeInterval(epoch))
            .formatted(date: .abbreviated, time: .shortened)
    }
}
