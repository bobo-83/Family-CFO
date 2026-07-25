import SwiftUI

/// The advisor on the wrist: dictate a question (watchOS text input is
/// dictation-first), watch the grounded loop narrate itself (ADR 0061), and
/// read the validated answer. Same streamed pipeline and saved-answer
/// recovery as the phone — a wrist-sized skin, not a second brain.
struct WatchChatView: View {
    @Environment(WatchModel.self) private var model
    @State private var draft = ""
    @State private var turns: [(role: String, text: String)] = []
    @State private var conversationID: String?
    @State private var progress: String?
    @State private var isSending = false
    @State private var errorMessage: String?
    @State private var speaker = WatchSpeaker()
    @State private var inConversation = false
    @State private var showHistory = false
    @AppStorage("family-cfo.watch.speakAnswers") private var speakAnswers = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    // These controls live IN content: a top-bar ToolbarItem
                    // (any placement) trips a UINavigationBar layout assertion
                    // on watchOS inside a paging TabView's NavigationStack —
                    // the scroll-to-chat crash (user reports 2026-07-25;
                    // bisected in the simulator rig).
                    HStack {
                        // ADR 0067 v4: past threads, and a clean slate.
                        Button {
                            showHistory = true
                        } label: {
                            Label("Chats", systemImage: "clock.arrow.circlepath")
                                .font(.caption2)
                        }
                        .buttonStyle(.borderless)
                        Button {
                            startNewChat()
                        } label: {
                            Label("New", systemImage: "square.and.pencil")
                                .font(.caption2)
                        }
                        .buttonStyle(.borderless)
                        .disabled(turns.isEmpty && conversationID == nil)
                        Spacer()
                        Button {
                            if speaker.isSpeaking {
                                speaker.stop()
                            } else {
                                speakAnswers.toggle()
                            }
                        } label: {
                            Label(
                                speakAnswers ? "Speaks answers" : "Muted",
                                systemImage: speakAnswers ? "speaker.wave.2.fill" : "speaker.slash"
                            )
                            .font(.caption2)
                        }
                        .buttonStyle(.borderless)
                    }
                    .labelStyle(.iconOnly)
                    if turns.isEmpty && !isSending {
                        Text("Ask about your money — \"can I afford new skis?\"")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    ForEach(Array(turns.enumerated()), id: \.offset) { index, turn in
                        Text(turn.text)
                            .font(.footnote)
                            .padding(6)
                            .frame(
                                maxWidth: .infinity,
                                alignment: turn.role == "user" ? .trailing : .leading
                            )
                            .background(
                                turn.role == "user"
                                    ? Color.blue.opacity(0.25) : Color.gray.opacity(0.2),
                                in: RoundedRectangle(cornerRadius: 8))
                            .id(index)
                            // Tap an answer to hear it; tap again to stop
                            // (user request 2026-07-25) — reopened threads
                            // and muted answers stay reachable by voice.
                            .onTapGesture {
                                guard turn.role == "assistant" else { return }
                                if speaker.isSpeaking {
                                    speaker.stop()
                                } else {
                                    Task { await speaker.speak(turn.text, api: model.speech) }
                                }
                            }
                    }
                    if isSending {
                        HStack(spacing: 4) {
                            ProgressView()
                            Text(progress ?? "Thinking…")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let errorMessage {
                        Text(errorMessage).font(.caption2).foregroundStyle(.red)
                    }
                }
            }
            .onChange(of: turns.count) {
                proxy.scrollTo(turns.count - 1, anchor: .bottom)
            }
            // Keep content clear of the vertical page-indicator dots on the
            // trailing edge (user report 2026-07-25: they sat on the speaker
            // toggle and the hint text).
            .contentMargins(.trailing, 10, for: .scrollContent)
        }
        .navigationTitle("Advisor")
        // Pushes are safe here; only top-bar toolbar items crash (ADR 0067 v3).
        .navigationDestination(isPresented: $showHistory) {
            WatchConversationsView { id in
                Task { await open(conversationID: id) }
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .bottomBar) {
                // CONVERSATION (user request 2026-07-25): tap once, talk,
                // hear the answer, and the mic re-opens for the follow-up —
                // the wrist version of the phone's voice mode, built on
                // programmatic dictation (watchOS has no Speech framework).
                Button {
                    if inConversation {
                        speaker.stop()
                        inConversation = false
                    } else {
                        Task { await runConversation() }
                    }
                } label: {
                    Label(
                        inConversation ? "End" : "Talk",
                        systemImage: inConversation ? "stop.circle.fill" : "mic.fill")
                }
                // Typing stays a first-class path: the same input sheet,
                // opened deliberately for scribble/keyboard.
                TextFieldLink(prompt: Text("Ask about your money")) {
                    Label("Type", systemImage: "keyboard")
                } onSubmit: { typed in
                    draft = typed
                    Task { await send() }
                }
            }
        }
    }

    /// A clean slate: the next question starts a fresh thread on the box.
    private func startNewChat() {
        speaker.stop()
        inConversation = false
        conversationID = nil
        turns = []
        errorMessage = nil
        progress = nil
    }

    /// Reopen a past thread: pull its turns from the box, then continue in it.
    private func open(conversationID id: String) async {
        guard let advisor = model.advisor else { return }
        startNewChat()
        do {
            let detail = try await advisor.conversation(id: id)
            conversationID = id
            turns = detail.messages
                .sorted { $0.sequence < $1.sequence }
                .map { (role: $0.role == .user ? "user" : "assistant", text: $0.content) }
        } catch {
            errorMessage = AdvisorErrorDescriber.describe(error)
        }
    }

    /// The hands-free loop: dictate, send, hear the answer, dictate again —
    /// until the user cancels the input sheet or taps End. In conversation
    /// the answer is always spoken, whatever the mute toggle says.
    private func runConversation() async {
        inConversation = true
        defer { inConversation = false }
        while inConversation {
            guard let utterance = await WatchDictation.ask(),
                !utterance.trimmingCharacters(in: .whitespaces).isEmpty
            else { break }  // cancelled — the conversation is over
            draft = utterance
            await send()
        }
    }

    private func send() async {
        let message = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty, !isSending, let advisor = model.advisor else { return }
        draft = ""
        turns.append(("user", message))
        isSending = true
        progress = nil
        defer {
            isSending = false
            progress = nil
        }
        do {
            let response = try await advisor.sendMessage(
                message, conversationID: conversationID, attachment: nil,
                onProgress: { detail in
                    Task { @MainActor in progress = detail }
                })
            conversationID = response.conversationId
            turns.append(("assistant", response.recommendation.answer))
            errorMessage = nil
            if speakAnswers || inConversation {
                await speaker.speak(response.recommendation.answer, api: model.speech)
            }
        } catch {
            // Same recovery as the phone: the box may have finished and saved.
            if let recovered = await SavedAnswerRecovery(api: advisor).poll(
                utterance: message, conversationID: conversationID)
            {
                conversationID = recovered.conversationID
                turns.append(("assistant", recovered.answer.content))
                errorMessage = nil
                if speakAnswers || inConversation {
                    await speaker.speak(recovered.answer.content, api: model.speech)
                }
            } else {
                errorMessage = "Couldn't get an answer — try again on WiFi."
            }
        }
    }
}
