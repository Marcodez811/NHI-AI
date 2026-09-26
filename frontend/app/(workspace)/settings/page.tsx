import Link from "next/link";

export default function SettingsPage() {
    return (
        <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
            <header className="mb-6 space-y-2">
                <h1 className="text-2xl font-semibold tracking-tight">設定</h1>
                <p className="text-sm leading-6 text-muted-foreground">
                    模型設定位於各工作流程頁面中，請由下方連結前往：
                </p>
            </header>
            <ul className="space-y-2 text-sm">
                <li>
                    <Link href="/slides?tab=settings" className="text-info hover:text-info/80">
                        簡報生成 → 模型設定
                    </Link>
                </li>
                <li>
                    <Link href="/news?tab=settings" className="text-info hover:text-info/80">
                        新聞稿生成 → 模型設定
                    </Link>
                </li>
            </ul>
        </div>
    );
}
