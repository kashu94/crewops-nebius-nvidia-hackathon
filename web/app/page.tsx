// Always land on sign-in. If there's already a session, that page offers "Continue".
import { redirect } from "next/navigation";

export default function Home() {
  redirect("/login");
}
