import SwiftUI

/// Shared input styling (M104) so text fields and icon buttons look the same
/// everywhere instead of falling back to the dated `.roundedBorder` box. Uses
/// semantic system colors so it adapts to light/dark automatically.
extension View {
    /// A soft, borderless rounded fill for a standalone text field.
    func roundedField(cornerRadius: CGFloat = 14) -> some View {
        self
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(
                Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }

    /// A subtle circular tap target for a composer icon (attach, mic).
    func composerIconButton() -> some View {
        self
            .font(.system(size: 18, weight: .regular))
            .frame(width: 38, height: 38)
            .background(Color(.secondarySystemBackground), in: Circle())
    }
}
