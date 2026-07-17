import Foundation
import UIKit

/// Photos shared into the app via the Share Extension (M102) land as files in an
/// App Group "inbox". The app drains it on foreground and lets the user attach
/// each to a transaction. Keeping the inbox in the app (not uploading from the
/// extension) means all networking, auth, and the transaction list stay in one
/// place. Dormant until the extension is wired — `directory` is nil without the
/// App Group, so `pending()` simply returns empty.
enum SharedPhotoInbox {
    static var directory: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: OverviewSnapshot.appGroup)?
            .appendingPathComponent("SharedInbox", isDirectory: true)
    }

    struct Item: Identifiable {
        let id: String
        let imageURL: URL
        /// An optional note the user typed on the share sheet.
        let note: String?
    }

    /// Shared images awaiting attachment, oldest first (share order).
    static func pending() -> [Item] {
        guard let dir = directory,
            let files = try? FileManager.default.contentsOfDirectory(
                at: dir, includingPropertiesForKeys: nil)
        else { return [] }
        return files
            .filter { $0.pathExtension.lowercased() == "jpg" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .map { url in
                let id = url.deletingPathExtension().lastPathComponent
                let noteURL = dir.appendingPathComponent("\(id).txt")
                let note = try? String(contentsOf: noteURL, encoding: .utf8)
                return Item(id: id, imageURL: url, note: note)
            }
    }

    static func hasPending() -> Bool { !pending().isEmpty }

    static func image(for item: Item) -> UIImage? {
        (try? Data(contentsOf: item.imageURL)).flatMap(UIImage.init(data:))
    }

    /// Remove an item once it's been attached (or discarded).
    static func remove(_ item: Item) {
        try? FileManager.default.removeItem(at: item.imageURL)
        if let dir = directory {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent("\(item.id).txt"))
        }
    }
}
