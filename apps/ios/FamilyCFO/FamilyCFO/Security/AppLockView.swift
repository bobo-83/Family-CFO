import SwiftUI

/// Face ID gate shown while the app holds a credential but the user hasn't
/// authenticated locally yet (M83).
struct AppLockView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "faceid")
                .font(.system(size: 56))
            // The household name is data; the product name is deliberately
            // never translated — so this whole line stays out of the catalog.
            Text(verbatim: model.server?.householdName ?? "Family CFO")
                .font(.title2.bold())
            Button("Unlock") {
                Task { await model.unlock() }
            }
            .buttonStyle(.borderedProminent)
        }
        .task { await model.unlock() }
    }
}
