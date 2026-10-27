import { FileDown } from "lucide-react";
import { urls } from "@/lib/api";
import { LinkButton } from "../ui";

export function SampleGuide({ open }: { open: boolean }) {
  return (
    <details open={open} className="group rounded-sheet border border-line bg-sheet">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-sheet px-5 py-4 [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block font-serif text-xl text-ink">How to collect good samples</span>
          <span className="mt-0.5 block text-[13.5px] text-muted">
            The printable sheet gives the most faithful letters. A page of normal writing helps with style.
          </span>
        </span>
        <span className="text-sm font-medium text-ink group-open:hidden">Show</span>
        <span className="hidden text-sm font-medium text-ink group-open:inline">Hide</span>
      </summary>
      <div className="grid gap-8 border-t border-line px-5 py-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div>
          <h3 className="text-lg">The template sheet</h3>
          <ol className="mt-3 flex list-decimal flex-col gap-1.5 pl-5 text-sm marker:text-ink-soft">
            <li>Download and print the sheet (A4, at 100% scale, no &ldquo;fit to page&rdquo;).</li>
            <li>Write one character in each box with the pen you normally use. Keep inside the box.</li>
            <li>Photograph or scan the whole page, including the four corner squares.</li>
            <li>Upload each page below as a template sample. Pages are recognised automatically.</li>
          </ol>
          <div className="mt-4">
            <LinkButton href={urls.templatePdf()} variant="primary" download icon={<FileDown className="size-4" aria-hidden />}>
              Download template sheet
            </LinkButton>
          </div>
          <h3 className="mt-6 text-lg">Free-form writing (optional)</h3>
          <p className="mt-2 text-sm">
            A page of your normal writing, ruled or unruled, with a typed transcript of each line. It is used to
            measure your slant, size, spacing and stroke thickness, and gives approximate letter shapes. Joined-up
            writing is cut apart into letters automatically, which is only approximate.
          </p>
        </div>
        <div className="flex flex-col gap-4">
          <div>
            <h3 className="text-lg">Cover every character</h3>
            <p className="mt-2 text-sm">
              Lowercase and capital letters, digits, punctuation and the maths symbols you use. Two or more copies of
              common letters make the writing look less repetitive.
            </p>
          </div>
          <div>
            <h3 className="text-lg">Take a clear picture</h3>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-sm marker:text-ink-soft">
              <li>Even daylight, no shadows of your hand or phone.</li>
              <li>Hold the camera flat above the page; fill the frame.</li>
              <li>High resolution, in focus. PNG, JPG or a PDF scan.</li>
              <li>Dark ink on white paper works best.</li>
            </ul>
          </div>
          <p className="rounded-[5px] bg-paper-deep px-3.5 py-3 text-[13px] text-muted">
            AssignmentHand writes by arranging the letter images captured from your own samples, with small variations.
            It is not a trained AI handwriting model, so characters you have not provided fall back to a printed font.
          </p>
        </div>
      </div>
    </details>
  );
}
