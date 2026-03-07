import AppKit
import Foundation

let outputPath = CommandLine.arguments.dropFirst().first

func printJSON(_ object: [String: Any]) {
    guard
        let data = try? JSONSerialization.data(withJSONObject: object, options: []),
        let text = String(data: data, encoding: .utf8)
    else {
        print("{\"ok\":false,\"reason\":\"json-encode-failed\"}")
        return
    }

    print(text)
}

guard let outputPath else {
    printJSON(["ok": false, "reason": "missing-output-path"])
    exit(0)
}

let pasteboard = NSPasteboard.general

if let pngData = pasteboard.data(forType: .png) {
    do {
        let outputURL = URL(fileURLWithPath: outputPath)
        try pngData.write(to: outputURL)
        printJSON(["ok": true, "path": outputPath])
    } catch {
        printJSON(["ok": false, "reason": "write-failed"])
    }
    exit(0)
}

guard
    let tiffData = pasteboard.data(forType: .tiff),
    let bitmap = NSBitmapImageRep(data: tiffData),
    let pngData = bitmap.representation(using: .png, properties: [:])
else {
    printJSON(["ok": false, "reason": "no-image"])
    exit(0)
}

do {
    let outputURL = URL(fileURLWithPath: outputPath)
    try pngData.write(to: outputURL)
    printJSON(["ok": true, "path": outputPath])
} catch {
    printJSON(["ok": false, "reason": "write-failed"])
}
