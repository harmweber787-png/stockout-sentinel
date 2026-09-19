import { LinkButton } from "@/components/Button";
import { Shell } from "@/components/Shell";

export default function WelcomePage() {
  return (
    <Shell>
      <section className="pt-6">
        <p className="text-xs uppercase tracking-widest text-accent-deep">Personal styling</p>
        <h1 className="mt-3 font-serif text-4xl leading-tight text-ink">
          Dress for the woman you already are.
        </h1>
        <p className="mt-4 text-[17px] leading-relaxed text-ink/80">
          Share two photos and get clothing and hair ideas chosen around your shape, your
          colouring and your features. Everything here is about what suits you, and there are no
          forms about weight or sizes.
        </p>
      </section>

      <ul className="mt-8 space-y-3">
        {[
          ["1", "Two photos", "One full-length, one of your face and hair. Phone photos are perfect."],
          ["2", "A quick read", "Your body shape, colour palette, face shape and hair texture."],
          ["3", "Six ideas", "Three outfits and three hairstyles, each with why it works for you."],
        ].map(([n, title, text]) => (
          <li
            key={n}
            className="flex gap-4 rounded-3xl bg-white p-4 shadow-soft ring-1 ring-line"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blush font-serif text-accent-deep">
              {n}
            </span>
            <div>
              <div className="font-medium text-ink">{title}</div>
              <div className="text-sm leading-relaxed text-muted">{text}</div>
            </div>
          </li>
        ))}
      </ul>

      <section className="mt-8 rounded-3xl bg-blush p-5">
        <h2 className="font-medium text-ink">Your photos stay yours</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-ink/80">
          Photos are analysed once and deleted immediately afterwards. We don&apos;t store them,
          and we don&apos;t build a record of you. If you like your results, you can choose to
          save a written style profile with your email address, and only the text is kept.
        </p>
      </section>

      <div className="mt-8">
        <LinkButton href="/upload" className="w-full">
          Let&apos;s begin
        </LinkButton>
      </div>
    </Shell>
  );
}
