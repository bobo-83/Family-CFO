import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        switch model.phase {
        case .loading:
            ProgressView()
        case .unpaired:
            PairingView()
        case .locked:
            AppLockView()
        case .ready:
            MainTabView()
        }
    }
}
