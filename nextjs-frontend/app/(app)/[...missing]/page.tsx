import { notFound } from "next/navigation";

/** Unknown paths inside the app render the not-found page within the shell. */
export default function Missing() {
  notFound();
}
