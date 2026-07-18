import Foundation

typealias HouseholdRole = Components.Schemas.HouseholdRole

/// What the shell shows (M83d, ADR 0034): every tab and Settings section is
/// gated by a RIGHT from the device credential — never by a role name. A
/// credential stored before rights shipped falls back to its legacy role's
/// preset, mirroring the server's mapping. Sign out is never gated.
struct RolePolicy: Equatable {
    let role: HouseholdRole?
    let rights: Set<String>

    init(role: HouseholdRole?, rights: Set<String>? = nil) {
        self.role = role
        self.rights = rights ?? Self.legacyRights(for: role)
    }

    /// The server's preset for each legacy tier (rights.py) — used only for
    /// credentials that predate rights.
    private static func legacyRights(for role: HouseholdRole?) -> Set<String> {
        switch role {
        case .owner:
            return [
                "finances.view", "advisor.use", "advisor.manage", "transactions.manage",
                "bills.manage", "budgets.manage", "goals.manage", "categories.manage",
                "income.manage", "imports.manage", "reports.manage", "accounts.manage",
                "connections.manage", "members.manage", "roles.manage", "devices.manage",
                "backups.manage", "audit.view", "household.settings.manage", "ai_runtime.manage",
            ]
        case .adult:
            return [
                "finances.view", "advisor.use", "advisor.manage", "transactions.manage",
                "bills.manage", "budgets.manage", "goals.manage", "categories.manage",
                "income.manage",
            ]
        case .viewer:
            return ["finances.view", "advisor.use"]
        case .child:
            return ["finances.view"]
        case nil:
            return []
        }
    }

    func has(_ right: String) -> Bool { rights.contains(right) }

    // MARK: Capabilities the shell asks about

    var canChat: Bool { has("advisor.use") }
    var canCategorize: Bool { has("transactions.manage") }
    var canManageBills: Bool { has("bills.manage") }
    var canManageBudgets: Bool { has("budgets.manage") }
    var canManageGoals: Bool { has("goals.manage") }
    /// Add/edit/remove accounts AND loans — a "User" deliberately lacks this.
    var canManageAccounts: Bool { has("accounts.manage") }
    var canViewActivity: Bool { has("audit.view") }
    var canManageBackups: Bool { has("backups.manage") }
    var canManageMembers: Bool { has("members.manage") }

    /// Legacy convenience still used by a few flows: any money-editing right.
    var canEditFinances: Bool {
        canCategorize || canManageBills || canManageBudgets || canManageGoals
    }

    /// Operator-ish: any admin surface (drives the Settings admin sections).
    var isOperator: Bool { canViewActivity || canManageBackups || canManageMembers }

    var displayName: String {
        switch role {
        case .owner: return "Admin"
        case .adult: return "User"
        case .viewer: return "Viewer"
        case .child: return "Child"
        case nil: return "Unknown"
        }
    }
}
