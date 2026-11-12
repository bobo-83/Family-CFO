import XCTest

final class SessionExpiryUITests: XCTestCase {
    @MainActor
    func testIdleExpiryRemovesProtectedTabsAndShowsSignIn() {
        let app = launch("--ui-expiring-session")
        let overview = app.tabBars.buttons["Overview"]
        XCTAssertTrue(overview.waitForExistence(timeout: 6))

        let notice = app.staticTexts["Your session has expired. Please sign in again."]
        XCTAssertTrue(notice.waitForExistence(timeout: 16))
        XCTAssertFalse(overview.exists)
        let email = app.textFields["Email"]
        let password = app.secureTextFields["Password"]
        XCTAssertTrue(email.waitForExistence(timeout: 5))

        email.tap()
        email.typeText("member@example.test")
        password.tap()
        password.typeText("wrong-password")
        app.buttons["Sign in"].tap()

        XCTAssertTrue(app.staticTexts[
            "Could not sign in on this device. Check your account details or set up this device again."
        ].waitForExistence(timeout: 5))
        XCTAssertTrue(notice.exists)

        password.tap()
        password.typeText("synthetic-password")
        app.buttons["Sign in"].tap()
        XCTAssertTrue(overview.waitForExistence(timeout: 7))
        XCTAssertTrue(overview.isSelected)
        XCTAssertFalse(notice.exists)
    }

    @MainActor
    func testOfflineAndVoluntarySignOutShowNoExpiryNotice() {
        let app = launch("--ui-offline-session")
        let overview = app.tabBars.buttons["Overview"]
        XCTAssertTrue(overview.waitForExistence(timeout: 6))
        overview.tap()
        XCTAssertTrue(overview.exists)
        let notice = app.staticTexts["Your session has expired. Please sign in again."]
        XCTAssertFalse(notice.exists)

        app.tabBars.buttons["More"].tap()
        let settings = app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        settings.tap()
        let logout = app.buttons["Sign out"]
        for _ in 0..<6 where !logout.isHittable { app.swipeUp() }
        XCTAssertTrue(logout.isHittable)
        logout.tap()
        app.alerts.buttons["Sign out"].tap()
        XCTAssertTrue(app.textFields["Email"].waitForExistence(timeout: 5))
        XCTAssertFalse(overview.exists)
        XCTAssertFalse(notice.exists)
    }

    @MainActor
    private func launch(_ mode: String) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = [mode, "-AppleLanguages", "(en)"]
        app.launch()
        return app
    }
}
