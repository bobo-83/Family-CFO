import SwiftUI

/// The small-slot cash meter (ADR 0067 v8): a pile of 1–5 banknotes for how
/// comfortable the month is, or a single torn bill when left-to-spend is
/// negative. Pure vector drawing — WidgetKit-safe at any complication size.
struct CashStackView: View {
    let signal: WatchFaceSnapshot.CashSignal

    var body: some View {
        GeometryReader { geometry in
            let size = min(geometry.size.width, geometry.size.height)
            ZStack {
                switch signal {
                case .torn:
                    TornBillView()
                        .frame(width: size * 0.85, height: size * 0.4)
                case .bills(let count):
                    BillPileView(count: max(1, min(count, 5)))
                        .frame(width: size * 0.8, height: size * 0.8)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
        }
    }
}

/// 1–5 notes stacked with a slight offset and alternating tilt, like a loose
/// pile of cash growing with the count.
private struct BillPileView: View {
    let count: Int

    var body: some View {
        GeometryReader { geometry in
            let billWidth = geometry.size.width * 0.92
            let billHeight = billWidth * 0.42
            let step = (geometry.size.height - billHeight) / CGFloat(max(count - 1, 1))
            ZStack {
                ForEach(0..<count, id: \.self) { index in
                    BillView(showsDollar: index == count - 1)
                        .frame(width: billWidth, height: billHeight)
                        .rotationEffect(.degrees(index.isMultiple(of: 2) ? -4 : 4))
                        .position(
                            x: geometry.size.width / 2,
                            y: geometry.size.height - billHeight / 2
                                - CGFloat(index) * min(step, billHeight * 0.55))
                }
            }
        }
    }
}

/// One banknote: rounded body, darker rim, center disc — a "$" on the top note.
private struct BillView: View {
    var showsDollar = false

    var body: some View {
        GeometryReader { geometry in
            let h = geometry.size.height
            ZStack {
                RoundedRectangle(cornerRadius: h * 0.18)
                    .fill(Color.green.gradient)
                RoundedRectangle(cornerRadius: h * 0.18)
                    .strokeBorder(Color.black.opacity(0.45), lineWidth: max(1, h * 0.07))
                Circle()
                    .strokeBorder(Color.black.opacity(0.35), lineWidth: max(0.8, h * 0.05))
                    .frame(width: h * 0.62, height: h * 0.62)
                if showsDollar {
                    Text("$")
                        .font(.system(size: h * 0.5, weight: .bold, design: .rounded))
                        .foregroundStyle(.black.opacity(0.55))
                }
            }
        }
    }
}

/// A bill ripped in two: halves pulled apart and tilted, jagged tear edges.
private struct TornBillView: View {
    var body: some View {
        GeometryReader { geometry in
            let w = geometry.size.width
            let h = geometry.size.height
            HStack(spacing: w * 0.06) {
                TornHalf(tornEdgeOnRight: true)
                    .rotationEffect(.degrees(-8))
                TornHalf(tornEdgeOnRight: false)
                    .rotationEffect(.degrees(8))
            }
            .frame(width: w, height: h)
        }
    }
}

private struct TornHalf: View {
    let tornEdgeOnRight: Bool

    var body: some View {
        GeometryReader { geometry in
            let shape = TornHalfShape(tornEdgeOnRight: tornEdgeOnRight)
            ZStack {
                shape.fill(Color.red.gradient.opacity(0.85))
                shape.stroke(Color.black.opacity(0.45), lineWidth: max(1, geometry.size.height * 0.07))
                if tornEdgeOnRight {
                    Text("$")
                        .font(
                            .system(
                                size: geometry.size.height * 0.5, weight: .bold, design: .rounded)
                        )
                        .foregroundStyle(.black.opacity(0.5))
                        .position(
                            x: geometry.size.width * 0.4, y: geometry.size.height / 2)
                }
            }
        }
    }
}

/// Half a note: three rounded corners and one zigzag tear edge.
private struct TornHalfShape: Shape {
    let tornEdgeOnRight: Bool

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let radius = rect.height * 0.18
        let teeth = 4
        let toothDepth = rect.width * 0.12

        func tearPoints(x: CGFloat, topToBottom: Bool) -> [CGPoint] {
            let ys = (0...teeth).map {
                rect.minY + rect.height * CGFloat($0) / CGFloat(teeth)
            }
            let ordered = topToBottom ? ys : ys.reversed()
            return ordered.enumerated().map { index, y in
                CGPoint(x: x + (index.isMultiple(of: 2) ? 0 : toothDepth * (tornEdgeOnRight ? -1 : 1)), y: y)
            }
        }

        if tornEdgeOnRight {
            path.move(to: CGPoint(x: rect.minX + radius, y: rect.minY))
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
            for point in tearPoints(x: rect.maxX, topToBottom: true).dropFirst() {
                path.addLine(to: point)
            }
            path.addLine(to: CGPoint(x: rect.minX + radius, y: rect.maxY))
            path.addArc(
                center: CGPoint(x: rect.minX + radius, y: rect.maxY - radius), radius: radius,
                startAngle: .degrees(90), endAngle: .degrees(180), clockwise: false)
            path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + radius))
            path.addArc(
                center: CGPoint(x: rect.minX + radius, y: rect.minY + radius), radius: radius,
                startAngle: .degrees(180), endAngle: .degrees(270), clockwise: false)
        } else {
            path.move(to: CGPoint(x: rect.maxX - radius, y: rect.minY))
            path.addArc(
                center: CGPoint(x: rect.maxX - radius, y: rect.minY + radius), radius: radius,
                startAngle: .degrees(270), endAngle: .degrees(0), clockwise: false)
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - radius))
            path.addArc(
                center: CGPoint(x: rect.maxX - radius, y: rect.maxY - radius), radius: radius,
                startAngle: .degrees(0), endAngle: .degrees(90), clockwise: false)
            for point in tearPoints(x: rect.minX, topToBottom: false).dropFirst() {
                path.addLine(to: point)
            }
        }
        path.closeSubpath()
        return path
    }
}
