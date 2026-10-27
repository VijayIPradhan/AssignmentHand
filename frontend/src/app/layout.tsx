import type { Metadata, Viewport } from "next";
import { Newsreader, Public_Sans } from "next/font/google";
import "./globals.css";

const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  display: "swap",
});

const publicSans = Public_Sans({
  variable: "--font-public-sans",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "AssignmentHand",
    template: "%s | AssignmentHand",
  },
  description: "Turn your handwriting samples and an assignment PDF into a handwritten, completed assignment.",
};

export const viewport: Viewport = {
  themeColor: "#fbfaf6",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${newsreader.variable} ${publicSans.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-paper text-graphite">{children}</body>
    </html>
  );
}
