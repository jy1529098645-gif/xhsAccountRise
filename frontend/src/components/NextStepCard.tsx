import { Link } from "react-router-dom";

interface Props {
  /** Big arrow + short next-step prompt. */
  label: string;
  hint?: string;
  to: string;
  emoji?: string;
}

export default function NextStepCard({ label, hint, to, emoji = "→" }: Props) {
  return (
    <div className="next-step-card">
      <div className="next-step-card-inner">
        <div>
          <div className="next-step-label">{label}</div>
          {hint && <div className="next-step-hint">{hint}</div>}
        </div>
        <Link to={to} className="next-step-btn">
          {emoji}
        </Link>
      </div>
    </div>
  );
}
