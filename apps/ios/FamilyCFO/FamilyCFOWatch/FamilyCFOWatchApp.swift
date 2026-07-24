import SwiftUI

/// Family CFO on the wrist (M-watch, ADR 0067): the Overview glance and a
/// dictation chat with the advisor. The watch is a THIN client — it receives
/// the paired server + credential from the phone over WatchConnectivity and
/// talks to the box directly with the same pinned TLS and bearer token.
@main
struct FamilyCFOWatchApp: App {
    @State private var model = WatchModel()

    var body: some Scene {
        WindowGroup {
            WatchRootView()
                .environment(model)
        }
    }
}
