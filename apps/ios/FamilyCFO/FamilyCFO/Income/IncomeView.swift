import SwiftUI

/// The Income tab: the analyzed income picture (detected sources, rollup, tax
/// estimate), the household's income earners, and the W-2 scan on-ramp — its
/// own first-class page, like Bills and Debts, instead of a camera shortcut
/// buried on Overview.
struct IncomeView: View {
    @State var viewModel: IncomeViewModel

    var body: some View {
        NavigationStack {
            Group {
                if let errorMessage = viewModel.errorMessage, viewModel.analysis == nil {
                    ContentUnavailableView {
                        Label("Can't load income", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else if let analysis = viewModel.analysis {
                    content(analysis)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Income")
            .task { await viewModel.load() }
            .refreshable { await viewModel.load() }
        }
    }

    private func content(_ analysis: Components.Schemas.IncomeAnalysisResponse) -> some View {
        List {
            Section("This year") {
                LabeledContent("Annual income", value: analysis.rollup.annualIncome.formatted)
                LabeledContent("Monthly average", value: analysis.rollup.monthlyAverage.formatted)
                LabeledContent("Estimated gross", value: analysis.tax.grossIncome.formatted)
            }

            if let warning = analysis.coverageWarning {
                Section {
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
            }

            if !analysis.sources.isEmpty {
                Section("Income sources") {
                    ForEach(analysis.sources, id: \.sourceKey) { source in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(source.name)
                            Text("\(source.typicalAmount.formatted) · \(source.frequency)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            Section {
                ForEach(viewModel.earners, id: \.id) { earner in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(earner.label)
                        Text("Base \(earner.baseSalary.formatted)/yr")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task { await viewModel.deleteEarner(earner) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                NavigationLink {
                    W2ScanView(viewModel: W2ScanViewModel(api: viewModel.api))
                } label: {
                    Label("Add earner (scan a W-2)", systemImage: "doc.text.viewfinder")
                }
            } header: {
                Text("Earners")
            } footer: {
                Text("Scan a W-2 to prefill an earner — or type the figures in by hand on the same screen. Swipe an earner to delete it.")
            }

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
    }
}
