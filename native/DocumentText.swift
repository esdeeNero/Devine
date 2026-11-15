import Foundation
import AppKit
import PDFKit
let url=URL(fileURLWithPath:CommandLine.arguments[1]); var result=""
if url.pathExtension.lowercased()=="pdf" {
    if let doc=PDFDocument(url:url) {
        for i in 0..<min(doc.pageCount,100) { result += (doc.page(at:i)?.string ?? "")+"\n"; if result.count>100000 {break} }
    } else { fputs("Не удалось открыть PDF",stderr);exit(1) }
} else {
    guard let text=try? NSAttributedString(url:url,options:[:],documentAttributes:nil) else {exit(1)}
    result=text.string
}
print(String(result.prefix(100000)))
