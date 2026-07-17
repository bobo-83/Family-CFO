import SwiftUI

/// The Activity/History screen (M101): a durable, scrollable log of every action
/// taken, newest first. Reversible actions (like recategorizing) keep an Undo
/// button here indefinitely — the transient undo bar is just a shortcut; this is
/// the place to change your mind later.
struct ActivityView: View {
    @State var viewModel: ActivityViewModel

    var body: some View {
        Group {
            if let errorMessage = viewModel.errorMessage, viewModel.isEmpty {
                ContentUnavailableView {
                    Label("Can't load activity", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(errorMessage)
                } actions: {
                    Button("Retry") { Task { await viewModel.load() } }
                        .buttonStyle(.borderedProminent)
                }
            } else if viewModel.isEmpty && !viewModel.isLoading {
                ContentUnavailableView(
                    "No activity yet",
                    systemImage: "clock.arrow.circlepath",
                    description: Text("Actions you take — categorizing, editing — will show up here."))
            } else {
                List {
                    if let errorMessage = viewModel.errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.red)
                    }
                    Section {
                        ForEach(viewModel.events) { event in
                            row(event)
                        }
                    } footer: {
                        Text("Reversible actions can be undone here at any time.")
                    }
                }
            }
        }
        .navigationTitle("Activity")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if viewModel.isLoading && viewModel.isEmpty { ProgressView() }
        }
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
    }

    @ViewBuilder private func row(_ event: Components.Schemas.AuditEvent) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(event.summary)
                    .font(.subheadline)
                    .foregroundStyle(event.revertedAt == nil ? .primary : .secondary)
                    .strikethrough(event.revertedAt != nil)
                Text(event.createdAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            trailing(event)
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder private func trailing(_ event: Components.Schemas.AuditEvent) -> some View {
        if event.revertedAt != nil {
            Label("Undone", systemImage: "arrow.uturn.backward")
                .labelStyle(.titleAndIcon)
                .font(.caption).foregroundStyle(.secondary)
        } else if event.undoable == true {
            if viewModel.undoingID == event.id {
                ProgressView()
            } else {
                Button("Undo") { Task { await viewModel.undo(event) } }
                    .font(.subheadline.weight(.semibold))
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
            }
        }
    }
}
