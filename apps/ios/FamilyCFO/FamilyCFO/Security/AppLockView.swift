import SwiftUI

/// Face ID gate shown while the app holds a credential but the user hasn't
/// authenticated locally yet (M83).
struct AppLockView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "faceid")
                .font(.system(size: 56))
            Text(model.server?.householdName ?? "Family CFO")
                .font(.title2.bold())
            Button("Unlock") {
                Task { await model.unlock() }
            }
            .buttonStyle(.borderedProminent)
        }
        .task { await model.unlock() }
    }
}
