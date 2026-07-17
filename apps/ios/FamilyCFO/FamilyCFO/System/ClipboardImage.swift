import UIKit

/// One shared reader for "paste a statement/photo" inputs (M114, ADR 0028):
/// every screen that accepts a statement image offers the clipboard, and they
/// all read it the same way. Apps stash a copied image differently — a UIImage,
/// an item provider, or typed data — and a copied PDF arrives as data; try each.
enum ClipboardImage {
    enum Contents {
        case image(UIImage)
        case pdf(Data)
        case none
    }

    static func read(_ deliver: @escaping @MainActor (Contents) -> Void) {
        let pasteboard = UIPasteboard.general
        if let data = pasteboard.data(forPasteboardType: "com.adobe.pdf") {
            Task { @MainActor in deliver(.pdf(data)) }
            return
        }
        if let image = pasteboard.image {
            Task { @MainActor in deliver(.image(image)) }
            return
        }
        if let provider = pasteboard.itemProviders.first(where: {
            $0.canLoadObject(ofClass: UIImage.self)
        }) {
            provider.loadObject(ofClass: UIImage.self) { object, _ in
                Task { @MainActor in
                    if let image = object as? UIImage {
                        deliver(.image(image))
                    } else {
                        deliver(.none)
                    }
                }
            }
            return
        }
        for type in ["public.png", "public.jpeg", "public.heic", "public.image"] {
            if let data = pasteboard.data(forPasteboardType: type),
                let image = UIImage(data: data)
            {
                Task { @MainActor in deliver(.image(image)) }
                return
            }
        }
        Task { @MainActor in deliver(.none) }
    }
}
