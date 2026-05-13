import { platformLabel } from "../format";

export default function PlatformPill({ platform }: { platform: string | undefined }) {
  if (!platform) return null;
  return <span className={`platform-pill ${platform}`}>{platformLabel(platform)}</span>;
}
