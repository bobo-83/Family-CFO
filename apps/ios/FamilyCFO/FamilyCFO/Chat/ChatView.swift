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
    @State private var voiceSession: VoiceSessionViewModel?
    /// Survives the cover's dismissal, which nils `voiceSession` before
    /// `onDismiss` runs — this is where the conversation the session started is
    /// read back from.
    @State private var endedVoiceSession: VoiceSessionViewModel?

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
                    let session = VoiceSessionViewModel(
                        api: viewModel.api,
                        conversationID: viewModel.conversationID,
                        engine: SpeechEngineFactory.make(),
                        synthesizer: SpeechSynthesizerFactory.make(speechAudio: model.speechAudio)
                    )
                    voiceSession = session
                    endedVoiceSession = session
                } label: {
                    Label("Voice conversation", systemImage: "waveform.circle")
                }
            }
        }
        .fullScreenCover(item: $voiceSession, onDismiss: adoptVoiceConversation) { session in
            VoiceConversationView(viewModel: session)
        }
        .task {
            await viewModel.loadHistory()
            await viewModel.sendQueuedMessageIfNeeded()
        }
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

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !viewModel.isSending
    }

    private var inputBar: some View {
        VStack(spacing: 8) {
            if let attachment = viewModel.pendingAttachment {
                HStack(spacing: 8) {
                    Label(attachment.displayName, systemImage: attachment.iconName)
                        .font(.caption)
                        .lineLimit(1)
                        .padding(.vertical, 6)
                        .padding(.horizontal, 12)
                        .background(Color(.secondarySystemBackground), in: Capsule())
                    Spacer()
                    Button {
                        viewModel.pendingAttachment = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal)
            }
            HStack(alignment: .bottom, spacing: 8) {
                attachmentMenu
                HStack(alignment: .bottom, spacing: 4) {
                    TextField(
                        isDictating ? "Listening…" : "Ask about your money…",
                        text: $draft, axis: .vertical
                    )
                    .lineLimit(1...5)
                    .padding(.vertical, 9)
                    Button {
                        toggleDictation()
                    } label: {
                        Image(systemName: isDictating ? "mic.fill" : "mic")
                            .font(.system(size: 18))
                            .foregroundStyle(isDictating ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
                            .symbolEffect(.pulse, isActive: isDictating)
                            .frame(width: 30, height: 34)
                    }
                }
                .padding(.leading, 16)
                .padding(.trailing, 6)
                .background(
                    Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                sendButton
            }
            .padding(.horizontal, 12)
            .padding(.top, 4)
            Text(Self.disclaimer)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
        }
        .background(.bar)
    }

    /// Always-visible advisor disclaimer (ADR 0031); kept in sync with the web
    /// client and DISCLAIMER.md.
    static let disclaimer =
        "Educational guidance from a local AI — not financial, tax, or legal "
        + "advice. It can be wrong; verify before acting."

    private var sendButton: some View {
        Button {
            stopDictation()
            let text = draft
            draft = ""
            Task { await viewModel.send(text) }
        } label: {
            Image(systemName: "arrow.up")
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(
                    canSend ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(Color(.systemGray4)),
                    in: Circle())
        }
        .disabled(!canSend)
        .animation(.easeInOut(duration: 0.15), value: canSend)
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
            Button {
                pasteAttachment()
            } label: {
                Label("Paste from clipboard", systemImage: "doc.on.clipboard")
            }
        } label: {
            Image(systemName: "plus")
                .foregroundStyle(.secondary)
                .composerIconButton()
        }
    }

    /// Attach whatever's on the clipboard (M118, ADR 0028) — a copied image or
    /// PDF lands exactly like a picked one.
    private func pasteAttachment() {
        let vm = viewModel
        ClipboardImage.read { contents in
            switch contents {
            case .image(let image):
                guard let data = image.jpegData(compressionQuality: 0.9) else {
                    attachmentError = "That image couldn't be processed."
                    return
                }
                do {
                    vm.pendingAttachment = try AttachmentTranscoder.image(
                        from: data, displayName: "Pasted image")
                } catch {
                    attachmentError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
                }
            case .pdf(let data):
                do {
                    vm.pendingAttachment = try AttachmentTranscoder.pdf(
                        from: data, displayName: "Pasted PDF")
                } catch {
                    attachmentError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
                }
            case .none:
                attachmentError = "There's no image or PDF on your clipboard to paste."
            }
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

    /// A hands-free session runs through the same grounded pipeline, so the box
    /// creates a real conversation. Adopt it here or the thread exists on the
    /// server and the app never shows it.
    private func adoptVoiceConversation() {
        guard let id = endedVoiceSession?.conversationID else { return }
        endedVoiceSession = nil
        Task { await viewModel.adopt(conversationID: id) }
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
                .textSelection(.enabled)
                .padding(12)
                .background(
                    message.author == .user
                        ? AnyShapeStyle(.tint.opacity(0.15))
                        : AnyShapeStyle(.fill.tertiary),
                    in: RoundedRectangle(cornerRadius: 14)
                )
                .contextMenu {
                    Button {
                        UIPasteboard.general.string = message.text
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                }
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
