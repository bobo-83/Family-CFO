import SwiftUI

/// The shared "Last synced 4 minutes ago" freshness line (M103). Dropped in the
/// same spot on every synced screen so the experience is identical — pull to
/// refresh runs the sync, this shows how fresh the bank data is. Renders nothing
/// until the first sync is known.
struct SyncStatusFooter: View {
    let status: SyncStatusModel

    var body: some View {
        if let text = status.lastSyncedText {
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}
