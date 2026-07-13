import Testing

@testable import FamilyCFO

struct RolePolicyTests {
    @Test func ownerIsOperatorAndCanEdit() {
        let policy = RolePolicy(role: .owner)
        #expect(policy.canChat)
        #expect(policy.canEditFinances)
        #expect(policy.isOperator)
    }

    @Test func adultEditsButDoesNotOperate() {
        let policy = RolePolicy(role: .adult)
        #expect(policy.canChat)
        #expect(policy.canEditFinances)
        #expect(!policy.isOperator)
    }

    @Test func viewerIsReadOnlyButMayChat() {
        let policy = RolePolicy(role: .viewer)
        #expect(policy.canChat)
        #expect(!policy.canEditFinances)
        #expect(!policy.isOperator)
    }

    @Test func unknownRoleIsMostRestrictive() {
        let policy = RolePolicy(role: nil)
        #expect(!policy.canEditFinances)
        #expect(!policy.isOperator)
        #expect(policy.displayName == "Unknown")
    }
}
