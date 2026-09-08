import SwiftUI

/// #156 / #158 review: a screen whose Add button waits for the household's
/// base currency says WHY it is waiting when the fetch failed, and offers the
/// retry on the spot — otherwise one transient 503 leaves the button disabled
/// for as long as the tab stays open, with nothing on screen to explain it.
struct CurrencyUnavailableBanner: View {
    let message: String
    let retry: () async -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Label {
                Text("Couldn't load the household currency: \(message)")
            } icon: {
                Image(systemName: "exclamationmark.triangle")
            }
            .font(.caption)
            .foregroundStyle(.orange)
            Spacer(minLength: 0)
            Button("Retry") { Task { await retry() } }
                .font(.caption.weight(.semibold))
                .buttonStyle(.bordered)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.bar)
    }
}
