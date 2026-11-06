import SwiftUI

struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(AppModel.self) private var model

    var loginViewModel: LoginViewModel? = nil

    var body: some View {
        content
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { model.checkSessionExpiry() }
            }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in
                model.checkSessionExpiry()
            }
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .loading:
            ProgressView()
        case .unpaired, .reauthenticating:
            if let loginViewModel { LoginView(viewModel: loginViewModel) } else { LoginView() }
        case .locked:
            AppLockView()
        case .ready:
            MainTabView(startAtOverview: model.startsAtOverview).id(model.sessionOwner)
        }
    }
}
