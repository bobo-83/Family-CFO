import SwiftUI

/// A searchable category picker (M96) — a grid of chips that fills the width
/// (two columns on a phone, more when wider) instead of one tall column. Each
/// chip carries an icon, and a merchant-aware "Recommended" pick sits at the top.
/// Reusable: give it the categories, the current selection, and a callback.
struct CategoryPickerSheet: View {
    let title: String
    let categories: [Components.Schemas.Category]
    let currentCategoryID: String?
    /// nil = uncategorize.
    let onSelect: (String?) -> Void
    /// When set, a "Create …" button appears for a typed name that doesn't exist.
    var onCreate: ((String) -> Void)? = nil
    /// The category to recommend when the merchant name matches nothing — the
    /// caller's most-used category, when it knows one. nil = no fallback (only a
    /// keyword match surfaces a recommendation).
    var recommendedFallback: Components.Schemas.Category? = nil
    /// When set, long-pressing a chip offers "Delete". The caller performs the
    /// delete + reload; deleting un-categorizes that category's transactions.
    var onDelete: ((Components.Schemas.Category) -> Void)? = nil

    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    /// Categories removed this session, hidden immediately for feedback while the
    /// caller's own reload catches up.
    @State private var deletedIDs: Set<String> = []
    @State private var pendingDelete: Components.Schemas.Category?

    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 10)]

    private var trimmedSearch: String { search.trimmingCharacters(in: .whitespaces) }

    private var availableCategories: [Components.Schemas.Category] {
        categories.filter { !deletedIDs.contains($0.id) }
    }

    /// The merchant-aware recommendation, resolved to one of `categories`.
    private var recommended: Components.Schemas.Category? {
        CategoryVisuals.recommendation(
            merchant: title, categories: availableCategories, fallback: recommendedFallback)
    }

    private var filtered: [Components.Schemas.Category] {
        guard !trimmedSearch.isEmpty else { return availableCategories }
        return availableCategories.filter { $0.name.localizedCaseInsensitiveContains(trimmedSearch) }
    }

    /// The grid excludes the recommended chip while searching is idle, so it
    /// isn't shown twice.
    private var gridCategories: [Components.Schemas.Category] {
        guard trimmedSearch.isEmpty, let rec = recommended else { return filtered }
        return filtered.filter { $0.id != rec.id }
    }

    private var canCreate: Bool {
        guard onCreate != nil, !trimmedSearch.isEmpty else { return false }
        return !availableCategories.contains {
            $0.name.localizedCaseInsensitiveCompare(trimmedSearch) == .orderedSame
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                if trimmedSearch.isEmpty, let rec = recommended {
                    recommendedSection(rec)
                }

                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(gridCategories, id: \.id) { category in
                        chip(category)
                    }
                }
                .padding(.horizontal)
                .padding(.top, 8)

                if canCreate, let onCreate {
                    Button {
                        onCreate(trimmedSearch)
                        dismiss()
                    } label: {
                        Label("Create “\(trimmedSearch)”", systemImage: "plus.circle.fill")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .padding(.horizontal)
                    .padding(.top, 8)
                } else if filtered.isEmpty {
                    ContentUnavailableView.search(text: search)
                        .padding(.top, 40)
                }

                if currentCategoryID != nil {
                    Button(role: .destructive) {
                        onSelect(nil)
                        dismiss()
                    } label: {
                        Label("Uncategorize", systemImage: "tag.slash")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.bordered)
                    .padding()
                }

                if onDelete != nil {
                    Label("Touch and hold a category to delete it.", systemImage: "hand.tap")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal)
                        .padding(.bottom, 12)
                }
            }
            .searchable(text: $search, prompt: "Search categories")
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .confirmationDialog(
            pendingDelete.map { "Delete “\($0.name)”?" } ?? "",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }),
            titleVisibility: .visible,
            presenting: pendingDelete
        ) { category in
            Button("Delete Category", role: .destructive) {
                deletedIDs.insert(category.id)
                onDelete?(category)
                pendingDelete = nil
            }
            Button("Cancel", role: .cancel) { pendingDelete = nil }
        } message: { _ in
            Text("Its transactions won't be deleted — they'll return to Uncategorized. Any budget for it is removed.")
        }
    }

    /// Long-press "Delete", only when the caller supports it.
    @ViewBuilder private func deleteMenu(_ category: Components.Schemas.Category) -> some View {
        if onDelete != nil {
            Button("Delete Category", systemImage: "trash", role: .destructive) {
                pendingDelete = category
            }
        }
    }

    private func recommendedSection(_ category: Components.Schemas.Category) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Recommended", systemImage: "sparkles")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Button {
                onSelect(category.id)
                dismiss()
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: CategoryVisuals.icon(for: category.name))
                        .font(.title3)
                        .frame(width: 28)
                    Text(category.name)
                        .font(.body.weight(.semibold))
                    Spacer()
                    if category.id == currentCategoryID {
                        Image(systemName: "checkmark.circle.fill")
                    }
                }
                .foregroundStyle(Color.accentColor)
                .padding(.vertical, 14)
                .padding(.horizontal, 16)
                .frame(maxWidth: .infinity)
                .background(Color.accentColor.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(Color.accentColor.opacity(0.5), lineWidth: 1)
                )
            }
            .buttonStyle(.plain)
            .contextMenu { deleteMenu(category) }
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    private func chip(_ category: Components.Schemas.Category) -> some View {
        let selected = category.id == currentCategoryID
        return Button {
            onSelect(category.id)
            dismiss()
        } label: {
            VStack(spacing: 6) {
                Image(systemName: CategoryVisuals.icon(for: category.name))
                    .font(.title3)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                Text(category.name)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(2)
                    .minimumScaleFactor(0.75)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 64)
            .padding(.vertical, 12)
            .padding(.horizontal, 8)
            .background(
                selected ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)
            )
            .foregroundStyle(selected ? Color.accentColor : .primary)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(alignment: .topTrailing) {
                if selected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .padding(6)
                }
            }
        }
        .buttonStyle(.plain)
        .contextMenu { deleteMenu(category) }
    }
}
