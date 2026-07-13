import Foundation

typealias HouseholdRole = Components.Schemas.HouseholdRole

/// What the shell shows for each household role (M83d). A paired device
/// always acts as the owner/adult who created the pairing session, but the
/// policy covers every contract role so future flows (e.g. viewer pairing)
/// slot in without UI rewrites.
struct RolePolicy: Equatable {
    let role: HouseholdRole?

    /// Everyone in the household can ask the advisor questions; the server
    /// enforces what the answer may touch.
    var canChat: Bool { role != .child }

    /// Mutating money data (categorization, confirmations — M90/M91) is for
    /// the adults.
    var canEditFinances: Bool { role == .owner || role == .adult }

    /// Device revocation and member management stay owner-only (and live on
    /// the web dashboard; the shell only points there).
    var isOperator: Bool { role == .owner }

    var displayName: String {
        switch role {
        case .owner: return "Owner"
        case .adult: return "Adult"
        case .viewer: return "Viewer"
        case .child: return "Child"
        case nil: return "Unknown"
        }
    }
}
