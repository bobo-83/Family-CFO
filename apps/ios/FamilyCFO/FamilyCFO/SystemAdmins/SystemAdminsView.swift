import SwiftUI

/// Box-level operator roster (ADR 0065): grant/revoke the users who may swap
/// the AI model and manage backups for every household on the box. Mirrors
/// the dashboard's Users page section.
struct SystemAdminsView: View {
    @State var viewModel: SystemAdminsViewModel
    @State private var grantEmail = ""
    @State private var confirmingRevoke: Components.Schemas.SystemAdmin?

    var body: some View {
        List {
            Section {
                if viewModel.isLoading && viewModel.admins.isEmpty {
                    HStack { ProgressView(); Text("Loading…").padding(.leading, 8) }
                }
                ForEach(viewModel.admins, id: \.userId) { admin in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(admin.displayName).font(.body)
                        Text(admin.email).font(.caption).foregroundStyle(.secondary)
                    }
                    .swipeActions {
                        if viewModel.canRevoke {
                            Button("Revoke", role: .destructive) {
                                confirmingRevoke = admin
                            }
                        }
                    }
                }
                if let error = viewModel.errorMessage {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            } header: {
                Text("Administrators")
            } footer: {
                Text(
                    viewModel.canRevoke
                        ? "Swipe to revoke. Changes take effect at the person's next sign-in."
                        : "The box must keep at least one system administrator."
                )
            }

            Section {
                TextField("Email of an existing user", text: $grantEmail)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button {
                    let email = grantEmail
                    grantEmail = ""
                    Task { await viewModel.grant(email: email) }
                } label: {
                    Label("Grant system admin", systemImage: "person.badge.key")
                }
                .disabled(
                    viewModel.isSubmitting
                        || grantEmail.trimmingCharacters(in: .whitespaces).isEmpty)
            } header: {
                Text("Grant")
            } footer: {
                Text(
                    "System administrators control machinery shared by every household on this box — which AI model answers, and backups of the whole database. Grants target existing users; invite new people from the dashboard first."
                )
            }
        }
        .navigationTitle("System admins")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .confirmationDialog(
            "Revoke system administrator?",
            isPresented: Binding(
                get: { confirmingRevoke != nil },
                set: { if !$0 { confirmingRevoke = nil } }),
            titleVisibility: .visible,
            presenting: confirmingRevoke
        ) { admin in
            Button("Revoke \(admin.displayName)", role: .destructive) {
                let id = admin.userId
                confirmingRevoke = nil
                Task { await viewModel.revoke(userID: id) }
            }
            Button("Cancel", role: .cancel) { confirmingRevoke = nil }
        } message: { admin in
            Text("\(admin.email) will no longer be able to swap the AI model or manage backups. This can be undone from Activity.")
        }
    }
}
