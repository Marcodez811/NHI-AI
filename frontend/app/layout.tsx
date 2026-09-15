import { Analytics } from "@vercel/analytics/next";
import type { Metadata, Viewport } from "next";
import { ThemeProvider } from "../components/theme-provider";
import { TooltipProvider } from "../components/ui/tooltip";
import "./globals.css";

export const metadata: Metadata = {
    title: "健保署 AI",
    description: "以文件為核心的內部 AI 工作台，支援對話與簡報生成。",
    icons: {
        icon: "/nhi-logo-small.png",
        apple: "/nhi-logo-small.png",
    },
};

export const viewport: Viewport = {
    colorScheme: "light dark",
    themeColor: [
        { media: "(prefers-color-scheme: light)", color: "#ffffff" },
        { media: "(prefers-color-scheme: dark)", color: "#171c1a" },
    ],
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="zh-Hant" className="bg-background" suppressHydrationWarning>
            <body className="antialiased">
                <ThemeProvider
                    attribute="class"
                    defaultTheme="light"
                    enableSystem
                    disableTransitionOnChange
                >
                    <TooltipProvider>
                        {children}
                    </TooltipProvider>
                </ThemeProvider>
                {process.env.NODE_ENV === "production" && <Analytics />}
            </body>
        </html>
    );
}
