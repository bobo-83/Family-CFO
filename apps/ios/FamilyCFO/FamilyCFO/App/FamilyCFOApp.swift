import SwiftUI

@main
struct FamilyCFOApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .task { model.bootstrap() }
        }
    }
}
