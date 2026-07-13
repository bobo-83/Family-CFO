import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// The advisor chat screen: grounded answers with confidence and warnings,
/// image and PDF attachments through the server's vision path (M84), and
/// CSV / spreadsheet / text attachments through its data-file preview (M85).
struct ChatView: View {
    /// Exactly the formats the server's data-file preview parses (M85):
    /// CSV/TSV, Excel workbooks, and plain text. Excel has no system UTType,
    /// so it is resolved by extension; `.spreadsheet` is deliberately NOT used
    /// because it would also offer Numbers files the server can't read.
    static let dataFileContentTypes: [UTType] = [
        .commaSeparatedText,
        .tabSeparatedText,
        .plainText,
        UTType(filenameExtension: "xlsx"),
        UTType(filenameExtension: "xlsm"),
    ].compactMap { $0 }

    @Environment(AppModel.self) private var model
    @State var viewModel: ChatViewModel
    @State private var draft = ""
    @State private var photoSelection: PhotosPickerItem?
    @State private var showingPDFImporter = false
    @State private var showingDataFileImporter = false
    @State private var showingCamera = false
    @State private var attachmentError: String?
    @State private var dictationEngine: SpeechEngine?
    @State private var isDictating = false
    @State private var showingVoiceConversation = false

    var body: some View {
        VStack(spacing: 0) {
            transcript
            inputBar
        }
        .navigationTitle("Advisor")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    stopDictation()
                    showingVoiceConversation = true
                } label: {
                    Label("Voice conversation", systemImage: "waveform.circle")
                }
            }
        }
        .fullScreenCover(isPresented: $showingVoiceConversation) {
            VoiceConversationView(
                viewModel: VoiceSessionViewModel(
                    api: viewModel.api,
                    conversationID: viewModel.conversationID,
                    engine: SpeechEngineFactory.make(),
                    synthesizer: SpeechSynthesizerFactory.make(speechAudio: model.speechAudio)
                )
            )
        }
        .task { await viewModel.loadHistory() }
        .onChange(of: photoSelection) { _, item in
            guard let item else { return }
            Task { await attachPhoto(item) }
        }
        .fileImporter(
            isPresented: $showingPDFImporter,
            allowedContentTypes: [.pdf]
        ) { result in
            attachPDF(result)
        }
        .fileImporter(
            isPresented: $showingDataFileImporter,
            allowedContentTypes: Self.dataFileContentTypes
        ) { result in
            attachDataFile(result)
        }
        .fullScreenCover(isPresented: $showingCamera) {
            CameraPicker { image in
                attachCameraImage(image)
            }
            .ignoresSafeArea()
        }
        .alert("Attachment", isPresented: .init(
            get: { attachmentError != nil },
            set: { if !$0 { attachmentError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(attachmentError ?? "")
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    if viewModel.isLoadingHistory {
                        ProgressView().padding(.top, 24)
                    }
                    ForEach(viewModel.messages) { message in
                        MessageBubble(message: message)
                            .id(message.id)
                    }
                    if viewModel.isSending {
                        HStack {
                            ProgressView()
                            Text("Thinking with your numbers…")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal)
                    }
                    if let errorMessage = viewModel.errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.red)
                            .padding(.horizontal)
                    }
                }
                .padding(.vertical, 12)
            }
            .onChange(of: viewModel.messages.count) {
                if let last = viewModel.messages.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var inputBar: some View {
        VStack(spacing: 6) {
            if let attachment = viewModel.pendingAttachment {
                HStack {
                    Label(attachment.displayName, systemImage: attachment.iconName)
                        .font(.caption)
                        .lineLimit(1)
                    Spacer()
                    Button {
                        viewModel.pendingAttachment = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal)
            }
            HStack(spacing: 8) {
                attachmentMenu
                Button {
                    toggleDictation()
                } label: {
                    Image(systemName: isDictating ? "mic.fill" : "mic")
                        .font(.title3)
                        .foregroundStyle(isDictating ? AnyShapeStyle(.red) : AnyShapeStyle(.tint))
                        .symbolEffect(.pulse, isActive: isDictating)
                }
                TextField(
                    isDictating ? "Listening…" : "Ask about your money…",
                    text: $draft, axis: .vertical
                )
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                Button {
                    stopDictation()
                    let text = draft
                    draft = ""
                    Task { await viewModel.send(text) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(
                    draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || viewModel.isSending
                )
            }
            .padding([.horizontal, .bottom])
        }
        .background(.bar)
    }

    private var attachmentMenu: some View {
        Menu {
            PhotosPicker(selection: $photoSelection, matching: .images) {
                Label("Photo library", systemImage: "photo.on.rectangle")
            }
            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                Button {
                    showingCamera = true
                } label: {
                    Label("Camera", systemImage: "camera")
                }
            }
            Button {
                showingPDFImporter = true
            } label: {
                Label("PDF", systemImage: "doc.richtext")
            }
            Button {
                showingDataFileImporter = true
            } label: {
                Label("Spreadsheet or CSV", systemImage: "tablecells")
            }
        } label: {
            Image(systemName: "paperclip")
                .font(.title3)
        }
    }

    private func attachPhoto(_ item: PhotosPickerItem) async {
        defer { photoSelection = nil }
        guard let data = try? await item.loadTransferable(type: Data.self) else {
            attachmentError = "That photo couldn't be loaded."
            return
        }
        do {
            viewModel.pendingAttachment = try AttachmentTranscoder.image(
                from: data, displayName: "Photo")
        } catch {
            attachmentError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }

    private func attachPDF(_ result: Result<URL, Error>) {
        attachPickedFile(result, unreadable: "That PDF couldn't be read.") { data, name in
            try AttachmentTranscoder.pdf(from: data, displayName: name)
        }
    }

    /// CSV / spreadsheet / text (M85) — the server turns it into a bounded
    /// grounded preview, so the answer cites real headers and sums.
    private func attachDataFile(_ result: Result<URL, Error>) {
        attachPickedFile(result, unreadable: "That file couldn't be read.") { data, name in
            try AttachmentTranscoder.dataFile(from: data, displayName: name)
        }
    }

    /// Files arrive from the importer security-scoped: the sandbox only grants
    /// access between start/stop, so the bytes must be read inside that window.
    private func attachPickedFile(
        _ result: Result<URL, Error>,
        unreadable: String,
        transcode: (Data, String) throws -> ChatAttachment
    ) {
        guard case .success(let url) = result else { return }
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else {
            attachmentError = unreadable
            return
        }
        do {
            viewModel.pendingAttachment = try transcode(data, url.lastPathComponent)
        } catch {
            attachmentError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }

    // MARK: Push-to-talk dictation (M86): transcribes on device into the
    // draft field, so the transcript is editable before sending.

    private func toggleDictation() {
        if isDictating {
            stopDictation()
        } else {
            startDictation()
        }
    }

    private func startDictation() {
        let engine = dictationEngine ?? SpeechEngineFactory.make()
        dictationEngine = engine
        Task {
            guard await engine.requestPermission() else {
                attachmentError =
                    "Allow microphone and speech recognition in Settings to dictate. Audio never leaves this phone."
                return
            }
            do {
                let prefix = draft.isEmpty ? "" : draft + " "
                let updates = try await engine.startTranscribing()
                isDictating = true
                for await text in updates {
                    draft = prefix + text
                }
            } catch {
                attachmentError =
                    (error as? LocalizedError)?.errorDescription ?? "Couldn't start dictation."
            }
            isDictating = false
        }
    }

    private func stopDictation() {
        dictationEngine?.stopTranscribing()
    }

    private func attachCameraImage(_ image: UIImage) {
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            attachmentError = "That photo couldn't be processed."
            return
        }
        do {
            viewModel.pendingAttachment = try AttachmentTranscoder.image(
                from: data, displayName: "Camera photo")
        } catch {
            attachmentError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }
}

/// One transcript row. Assistant markdown renders through AttributedString;
/// grounded metadata (confidence, warnings, impacts) rides below the text.
struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        VStack(alignment: message.author == .user ? .trailing : .leading, spacing: 6) {
            if let attachmentName = message.attachmentName {
                Label(attachmentName, systemImage: "paperclip")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text(markdown: message.text)
                .padding(12)
                .background(
                    message.author == .user
                        ? AnyShapeStyle(.tint.opacity(0.15))
                        : AnyShapeStyle(.fill.tertiary),
                    in: RoundedRectangle(cornerRadius: 14)
                )
            if !message.warnings.isEmpty {
                ForEach(message.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
            }
            if let confidence = message.confidence {
                Text("Confidence \(Int((confidence * 100).rounded()))%")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(
            maxWidth: .infinity,
            alignment: message.author == .user ? .trailing : .leading
        )
        .padding(.horizontal)
    }
}

extension Text {
    /// Markdown when it parses, plain text when it doesn't — the advisor's
    /// answers are markdown by contract but history may hold anything.
    init(markdown: String) {
        if let attributed = try? AttributedString(
            markdown: markdown,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            self.init(attributed)
        } else {
            self.init(verbatim: markdown)
        }
    }
}
