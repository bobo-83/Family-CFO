import SwiftUI
import UIKit

extension View {
    /// Adds a "Done" button above the keyboard. Number/decimal pads have no Return
    /// key, so without this there's no way to dismiss them (and commit the value).
    func keyboardDoneButton() -> some View {
        toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") {
                    UIApplication.shared.sendAction(
                        #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                }
            }
        }
    }
}
