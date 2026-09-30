import type { Metadata } from "next";

import { Auth } from "@/app/openapi-client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getApiClient } from "@/lib/api/server";

export const metadata: Metadata = { title: "Overview" };

export default async function OverviewPage() {
  const { data: me, error } = await Auth.me({ client: await getApiClient() });

  if (error || !me) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load your account. Try reloading the page.
      </p>
    );
  }

  const namespaces = Object.entries(me.roles).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  return (
    <div className="grid max-w-3xl gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">
        Welcome{me.user.name ? `, ${me.user.name}` : ""}
      </h1>

      <Card>
        <CardHeader>
          <CardTitle>Your account</CardTitle>
          <CardDescription>{me.user.email}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {me.user.admin && <Badge>Administrator</Badge>}
          <Badge variant="outline">
            {me.scope === "write" ? "Read & write" : "Read only"}
          </Badge>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Namespaces</CardTitle>
          <CardDescription>
            The parts of the archive you can open, and your role in each.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {namespaces.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              You don&apos;t have access to any namespace yet. Ask an
              administrator to add you.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Namespace</TableHead>
                  <TableHead>Role</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {namespaces.map(([name, role]) => (
                  <TableRow key={name}>
                    <TableCell className="font-medium">{name}</TableCell>
                    <TableCell className="capitalize">{role}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
