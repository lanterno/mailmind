#if os(macOS)
import AppKit
import SwiftUI

/// The first run: no account yet. A welcome in the brief's manner (#44): the painted meadow across the top with the
/// greeting on it, a card in the brief's hand saying what MailMind will do, and the two steps as cards: connect an
/// account (the page's `firstrun`), and what AI needs, a key for the provider chosen (Claude unless another was), or
/// nothing, and AI stays off.
struct Welcome: View {
    let status: AppStatus
    /// Whether the window's traffic lights stand over the scene's top left; a test's picture has none.
    var lights = true
    @State private var adding = false
    @State private var kept: Set<API.AiProvider>?
    @Environment(\.meadow) private var meadow

    init(status: AppStatus, kept: Set<API.AiProvider>? = nil, lights: Bool = true) {
        self.status = status
        self.lights = lights
        _kept = State(initialValue: kept)
    }

    private var provider: API.AiProvider { status.preferences?.aiProvider ?? .anthropic }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                hero
                VStack(alignment: .leading, spacing: 0) {
                    letter
                        .padding(.top, -84)
                    Text("Two steps to start")
                        .font(.meadow(.display, 19, .bold))
                        .foregroundStyle(meadow.ink)
                        .padding(.top, 28)
                        .padding(.bottom, 12)
                        .accessibilityAddTraits(.isHeader)
                    VStack(spacing: 10) {
                        WelcomeStep(number: 1, title: String(localized: "Connect your first account"),
                                    text: Self.connecting) {
                            Button { adding = true } label: {
                                MeadowIconView(.plus, size: 15)
                                Text("Connect Your First Account…")
                            }
                            .meadowButton(.primary)
                            .keyboardShortcut(.defaultAction)
                        }
                        WelcomeStep(number: 2, title: String(localized: "AI, if you want it"), text: Self.ai) {
                            VStack(alignment: .leading, spacing: 10) {
                                if provider.takesKey {
                                    AIKeyView(status: status, provider: provider, kept: $kept)
                                }
                                SettingHelp(String(localized: """
                                    Another provider, or Ollama on this Mac, is chosen in Settings › AI.
                                    """))
                                SettingHelp(AIWords.privacy(provider))
                            }
                        }
                    }
                }
                .frame(maxWidth: 800, alignment: .leading)
                .padding(.horizontal, 40)
                .padding(.bottom, 56)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .scrollContentBackground(.hidden)
        .background {
            ZStack {
                meadow.paper
                MeadowGrain()
            }
            .ignoresSafeArea()
        }
        .sheet(isPresented: $adding) {
            AccountForm(status: status, draft: AccountDraft()) { _ in adding = false }
        }
    }

    private static let connecting = String(localized: """
        Gmail, Proton through Proton Bridge, or any IMAP server. Its password is kept in your login Keychain, never \
        in a file.
        """)

    private static let ai = String(localized: """
        Summaries, the morning brief, drafted replies and asking your mail are written by the AI service you \
        choose: Claude, OpenAI or Gemini with an API key of yours, or a model running on this Mac. Without one, AI \
        is off and nothing is sent: everything else works as it does with it.
        """)

    /// The scene across the top (`.bhero`), with the greeting on its sky. It covers the whole window, so with the
    /// traffic lights over its corner the scene and the greeting move down by their room.
    private var hero: some View {
        let room: CGFloat = lights ? 36 : 0
        return MeadowScene(seed: 11)
            .frame(height: 220 + room)
            .overlay(alignment: .topLeading) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("WELCOME")
                        .font(.meadow(.ui, 12, .heavy))
                        .tracking(12 * 0.14)
                        .foregroundStyle(meadow.ink2)
                        .accessibilityHidden(true)
                    Text("Welcome to MailMind.")
                        .font(.meadow(.display, 40, .bold))
                        .foregroundStyle(meadow.ink)
                        .accessibilityAddTraits(.isHeader)
                }
                .padding(.leading, 40)
                .padding(.top, 32 + room)
            }
    }

    /// The card over the scene's foot (`.brief-card`), in the brief's hand.
    private var letter: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                MeadowIconView(.leaf, size: 16).foregroundStyle(meadow.moss)
                MeadowSectionLabel(String(localized: "No accounts yet"))
            }
            Text("""
                No email accounts connected yet. Connect one and MailMind reads it here, on this Mac: a brief each \
                morning, what needs you at the top, and everything else filed where it belongs.
                """)
                .font(.meadow(.hand, 18, .semibold))
                .foregroundStyle(meadow.moss)
                .lineSpacing(MeadowFace.hand.lineSpacing(size: 18, lineHeight: 1.7))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(EdgeInsets(top: 20, leading: 24, bottom: 18, trailing: 24))
        .frame(maxWidth: .infinity, alignment: .leading)
        .meadowPanel(radius: 18, raised: true)
    }
}

/// A step of the first run as a card (`.need`): its number on a tile, what it is, why, and what to do.
struct WelcomeStep<Action: View>: View {
    let number: Int
    let title: String
    let text: String
    @ViewBuilder let action: () -> Action
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(verbatim: "\(number)")
                .font(.meadow(.display, 17, .bold))
                .foregroundStyle(meadow.ink2)
                .frame(width: 34, height: 34)
                .background(meadow.paper3, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Text(title)
                    .font(.meadow(.ui, 15.5, .bold))
                    .foregroundStyle(meadow.ink)
                    .padding(.top, 6)
                    .accessibilityAddTraits(.isHeader)
                Text(text)
                    .font(.meadow(.ui, 14))
                    .foregroundStyle(meadow.ink2)
                    .lineSpacing(MeadowFace.ui.lineSpacing(size: 14, lineHeight: 1.5))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                action()
                    .padding(.top, 12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(meadow.paperRaised, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(meadow.line))
        .accessibilityElement(children: .contain)
    }
}
#endif
