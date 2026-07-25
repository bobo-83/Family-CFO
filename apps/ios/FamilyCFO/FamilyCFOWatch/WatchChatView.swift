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
    @AppStorage("family-cfo.watch.speakAnswers") private var speakAnswers = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    // Speaker toggle lives IN content: a top-bar ToolbarItem
                    // (any placement) trips a UINavigationBar layout assertion
                    // on watchOS inside a paging TabView's NavigationStack —
                    // the scroll-to-chat crash (user reports 2026-07-25;
                    // bisected in the simulator rig).
                    HStack {
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
        }
        .navigationTitle("Advisor")
        .toolbar {
            ToolbarItem(placement: .bottomBar) {
                // TextFieldLink opens the watch input UI, which is
                // dictation-first: one tap, talk, done — the wrist version of
                // the phone's voice mode. Answers are spoken back.
                TextFieldLink(prompt: Text("Ask about your money")) {
                    Label("Ask", systemImage: "mic.fill")
                } onSubmit: { spoken in
                    draft = spoken
                    Task { await send() }
                }
            }
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
            if speakAnswers {
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
                if speakAnswers {
                    await speaker.speak(recovered.answer.content, api: model.speech)
                }
            } else {
                errorMessage = "Couldn't get an answer — try again on WiFi."
            }
        }
    }
}
