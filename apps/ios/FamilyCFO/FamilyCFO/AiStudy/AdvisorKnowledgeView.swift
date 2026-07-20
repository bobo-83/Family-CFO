import SwiftUI

/// How much of the family's history the advisor has studied (ADR 0040), and the
/// durable insights it distilled. Knowledge is stored as reviewable notes on the
/// box — never trained into model weights — so it stays current and deletable.
struct AdvisorKnowledgeView: View {
    @State var viewModel: AiStudyViewModel

    var body: some View {
        Group {
            if let errorMessage = viewModel.errorMessage, viewModel.status == nil {
                ContentUnavailableView {
                    Label("Can't load advisor knowledge", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(errorMessage)
                } actions: {
                    Button("Retry") { Task { await viewModel.load() } }
                        .buttonStyle(.borderedProminent)
                }
            } else if let status = viewModel.status {
                content(status)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Advisor knowledge")
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }

    private func content(_ status: Components.Schemas.AiStudyStatus) -> some View {
        Form {
            if status.totalMonths == 0 {
                Section {
                    Text("Nothing to study yet — add or import transactions first.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("\(status.coveragePercent)%")
                                .font(.title2.bold())
                            Spacer()
                            if let last = status.lastStudiedAt {
                                Text("Last studied \(last.formatted(.relative(presentation: .named)))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        ProgressView(value: viewModel.coverageFraction)
                            .tint(status.coveragePercent >= 100 ? .green : .accentColor)
                        Text("Studied \(status.studiedMonths) of \(status.totalMonths) complete months of your history.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                    if !status.runtimeUsable {
                        Label(
                            "Paused — pick and enable an AI model on the web dashboard to resume.",
                            systemImage: "pause.circle"
                        )
                        .font(.callout)
                        .foregroundStyle(.orange)
                    }
                } header: {
                    Text("Knowledge of your data")
                } footer: {
                    Text("While nobody is chatting, the advisor studies your history one month at a time and remembers the patterns — spending habits, income rhythm, seasonal costs.")
                }
            }
            if !status.insights.isEmpty {
                Section {
                    ForEach(status.insights, id: \.key) { insight in
                        Text(insight.value)
                            .font(.callout)
                    }
                } header: {
                    Text("What it has learned")
                } footer: {
                    Text("Stored as reviewable notes, never trained into the model — so knowledge stays current and deletable.")
                }
            }
        }
    }
}
