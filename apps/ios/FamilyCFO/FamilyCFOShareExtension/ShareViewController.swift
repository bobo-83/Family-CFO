import Social
import UIKit
import UniformTypeIdentifiers

/// The Share Extension (M102): accept a photo shared from another app (Photos,
/// Messages, Mail) and drop it into the App Group inbox. The main app drains the
/// inbox on next launch and lets you attach each photo to a transaction — that
/// keeps all the networking, auth, and the transaction list in the app, so the
/// extension stays tiny and offline.
final class ShareViewController: SLComposeServiceViewController {
    private let appGroup = "group.com.familycfo.ios"

    override func isContentValid() -> Bool { true }

    override func presentationAnimationDidFinish() {
        placeholder = "Optional note (we'll also read the check for you)"
    }

    override func didSelectPost() {
        let note = (contentText ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let providers = ((extensionContext?.inputItems as? [NSExtensionItem]) ?? [])
            .flatMap { $0.attachments ?? [] }
            .filter { $0.hasItemConformingToTypeIdentifier(UTType.image.identifier) }

        let group = DispatchGroup()
        for (index, provider) in providers.enumerated() {
            group.enter()
            provider.loadItem(forTypeIdentifier: UTType.image.identifier, options: nil) {
                [weak self] item, _ in
                defer { group.leave() }
                if let data = Self.imageData(from: item) {
                    // Only tag the first image with the typed note.
                    self?.save(data, note: index == 0 ? note : "")
                }
            }
        }
        group.notify(queue: .main) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
        }
    }

    private static func imageData(from item: NSSecureCoding?) -> Data? {
        if let url = item as? URL, let data = try? Data(contentsOf: url) { return data }
        if let data = item as? Data { return data }
        if let image = item as? UIImage { return image.jpegData(compressionQuality: 0.85) }
        return nil
    }

    private func save(_ data: Data, note: String) {
        guard
            let container = FileManager.default.containerURL(
                forSecurityApplicationGroupIdentifier: appGroup)
        else { return }
        let dir = container.appendingPathComponent("SharedInbox", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        // A timestamp-ordered id keeps the app's inbox in share order.
        let id = "\(Int(Date().timeIntervalSince1970 * 1000))-\(UUID().uuidString.prefix(8))"
        let jpeg = UIImage(data: data)?.jpegData(compressionQuality: 0.85) ?? data
        try? jpeg.write(to: dir.appendingPathComponent("\(id).jpg"))
        if !note.isEmpty {
            try? note.data(using: .utf8)?.write(to: dir.appendingPathComponent("\(id).txt"))
        }
    }
}
