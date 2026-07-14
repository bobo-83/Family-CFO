import SwiftUI

/// Hands-free voice conversation (M86): speak, get spoken grounded answers,
/// keep talking. Tap the orb to interrupt an answer; swipe down or End to
/// leave.
struct VoiceConversationView: View {
    @State var viewModel: VoiceSessionViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            orb
                .onTapGesture {
                    viewModel.interruptSpeech()
                }

            Text(statusLine)
                .font(.headline)
                .foregroundStyle(.secondary)

            ScrollView {
                VStack(spacing: 12) {
                    if !viewModel.transcript.isEmpty {
                        Text(viewModel.transcript)
                            .font(.title3)
                            .multilineTextAlignment(.center)
                    } else if let answer = viewModel.lastAnswer {
                        Text(markdown: answer)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }
                .padding(.horizontal, 24)
            }
            .frame(maxHeight: 220)

            Spacer()

            if case .denied = viewModel.phase {
                Text("Allow microphone and speech recognition in Settings to talk to your CFO. Audio never leaves this phone.")
                    .font(.caption)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
            if case .failed(let message) = viewModel.phase {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Button("End conversation") {
                viewModel.end()
                dismiss()
            }
            .buttonStyle(.bordered)
            .padding(.bottom, 24)
        }
        .task { await viewModel.begin() }
        // Keep the screen awake for the whole hands-free conversation: otherwise
        // auto-lock suspends the app mid-answer and cuts the spoken reply off.
        // Restored when the conversation screen goes away.
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
            viewModel.end()
        }
    }

    private var statusLine: String {
        switch viewModel.phase {
        case .idle: return "Starting…"
        case .listening: return "Listening — just talk"
        case .thinking: return "Thinking with your numbers…"
        case .speaking: return "Tap the circle to interrupt"
        case .denied: return "Microphone access needed"
        case .failed: return "Something went wrong"
        }
    }

    private var orb: some View {
        ZStack {
            Circle()
                .fill(orbGradient)
                .frame(width: 140, height: 140)
                .scaleEffect(orbScale)
                .animation(
                    .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                    value: orbScale
                )
            Image(systemName: orbSymbol)
                .font(.system(size: 44))
                .foregroundStyle(.white)
        }
    }

    private var orbScale: CGFloat {
        switch viewModel.phase {
        case .listening: return 1.12
        case .speaking: return 1.06
        default: return 1.0
        }
    }

    private var orbSymbol: String {
        switch viewModel.phase {
        case .listening: return "waveform"
        case .thinking: return "ellipsis"
        case .speaking: return "speaker.wave.2.fill"
        case .denied, .failed: return "mic.slash"
        case .idle: return "mic"
        }
    }

    private var orbGradient: LinearGradient {
        LinearGradient(
            colors: viewModel.phase == .listening
                ? [Color(red: 0.2, green: 0.83, blue: 0.6), Color(red: 0.05, green: 0.4, blue: 0.35)]
                : [Color(red: 0.06, green: 0.2, blue: 0.31), Color(red: 0.04, green: 0.29, blue: 0.27)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
