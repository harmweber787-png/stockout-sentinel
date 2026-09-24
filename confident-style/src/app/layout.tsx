import type { Metadata, Viewport } from "next";
import "./globals.css";
import { StyleSessionProvider } from "@/lib/session-context";

export const metadata: Metadata = {
  title: "Confident Style",
  description:
    "Personal clothing and hair advice from two photos. Warm, practical, and built around what already suits you.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#FBF5F0",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <StyleSessionProvider>{children}</StyleSessionProvider>
      </body>
    </html>
  );
}
