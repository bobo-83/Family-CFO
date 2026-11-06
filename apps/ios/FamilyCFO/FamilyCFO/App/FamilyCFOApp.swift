import SwiftUI

@main
struct FamilyCFOApp: App {
    @State private var model = AppModel()
    #if DEBUG && targetEnvironment(simulator)
    @State private var sessionUIFixture = SessionUIFixture.fromLaunchArguments()
    #endif

    var body: some Scene {
        WindowGroup {
            #if DEBUG && targetEnvironment(simulator)
            if let sessionUIFixture {
                RootView(loginViewModel: sessionUIFixture.login)
                    .environment(sessionUIFixture.model)
                    .task { sessionUIFixture.start() }
            } else {
                normalRoot
            }
            #else
            normalRoot
            #endif
        }
    }
    private var normalRoot: some View {
        RootView()
            .environment(model)
            .task { model.bootstrap() }
    }
}
