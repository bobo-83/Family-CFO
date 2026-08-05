import SwiftUI
import UIKit

/// The system share/save sheet, presentable programmatically.
///
/// SwiftUI's `ShareLink` is a *button* — it needs its own tap. When an action
/// already produced the file (the household export, #189), the sheet should
/// present itself instead of asking for a second tap, which needs the UIKit
/// controller behind a representable.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
