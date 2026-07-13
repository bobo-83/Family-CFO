import PhotosUI
import SwiftUI
import UIKit

/// W-2 scan → confirm → add earner (M89). The scan is the on-ramp, not the
/// save: every value stays editable and nothing is written until "Add earner".
struct W2ScanView: View {
    @Environment(\.dismiss) private var dismiss
    @State var viewModel: W2ScanViewModel
    @State private var showingCamera = false
    @State private var photoSelection: PhotosPickerItem?

    var body: some View {
        Form {
            Section {
                if UIImagePickerController.isSourceTypeAvailable(.camera) {
                    Button {
                        showingCamera = true
                    } label: {
                        Label("Photograph the W-2", systemImage: "camera")
                    }
                }
                PhotosPicker(selection: $photoSelection, matching: .images) {
                    Label("Choose from library", systemImage: "photo.on.rectangle")
                }
                if viewModel.isScanning {
                    HStack {
                        ProgressView()
                        Text("Reading the form on the box…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Scan")
            } footer: {
                Text("The scan only fills in the form below. Nothing is saved until you tap Add earner — check every figure first.")
            }

            if let note = viewModel.scanNote {
                Section("What the scan read") {
                    Text(note)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Earner") {
                TextField("Label (e.g. employer or name)", text: $viewModel.form.label)
                yearField
            }

            Section("W-2 actuals") {
                moneyField("Box 1 — wages", value: $viewModel.form.wages)
                moneyField("Box 2 — federal withheld", value: $viewModel.form.withheld)
            }

            if let errorMessage = viewModel.errorMessage {
                Section {
                    Label(errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.red)
                }
            }

            Section {
                Button {
                    Task {
                        await viewModel.addEarner()
                        if viewModel.didSave { dismiss() }
                    }
                } label: {
                    if viewModel.isSaving {
                        ProgressView()
                    } else {
                        Text("Add earner")
                    }
                }
                .disabled(!viewModel.canSave)
            }
        }
        .navigationTitle("Scan W-2")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") { dismiss() }
            }
        }
        .fullScreenCover(isPresented: $showingCamera) {
            CameraPicker { image in
                Task { await viewModel.scan(image) }
            }
            .ignoresSafeArea()
        }
        .onChange(of: photoSelection) { _, item in
            guard let item else { return }
            Task {
                defer { photoSelection = nil }
                guard let data = try? await item.loadTransferable(type: Data.self),
                    let image = UIImage(data: data)
                else { return }
                await viewModel.scan(image)
            }
        }
    }

    private var yearField: some View {
        TextField(
            "Tax year",
            value: $viewModel.form.year,
            format: .number.grouping(.never)
        )
        .keyboardType(.numberPad)
    }

    private func moneyField(_ title: String, value: Binding<Decimal?>) -> some View {
        TextField(title, value: value, format: .currency(code: "USD"))
            .keyboardType(.decimalPad)
    }
}
