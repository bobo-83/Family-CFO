import SwiftUI

struct WatchRootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(WatchModel.self) private var model

    var body: some View {
        content
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { model.checkExpiry() }
            }
    }

    @ViewBuilder private var content: some View {
        if model.isPaired {
            TabView {
                NavigationStack { WatchGlanceView() }
                NavigationStack { WatchYearTrendView() }
                NavigationStack { WatchBudgetsView() }
                NavigationStack { WatchChatView() }
            }
            .tabViewStyle(.verticalPage)
            .id(model.sessionRevision)
        } else {
            VStack(spacing: 8) {
                Image(systemName: "iphone.and.arrow.forward")
                    .font(.title2)
                Text("Open Family CFO on your iPhone to restore Watch access.")
                    .font(.footnote)
                    .multilineTextAlignment(.center)
                Button("Reconnect to iPhone") { Task { await model.reconnectToPhone() } }
            }
            .padding()
        }
    }
}
