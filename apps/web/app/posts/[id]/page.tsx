import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { PublicPostPage } from "@/components/public-post-page";
import { ApiRequestError } from "@/lib/api";
import { fetchPublicPost } from "@/lib/posts-api";
import { publicMarkdownAlternate } from "@/lib/public-document";

export const dynamic = "force-dynamic";
const getPost = cache(fetchPublicPost);

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  try {
    const post = await getPost(id); const path = `/posts/${encodeURIComponent(post.id)}`;
    return { title: post.title, description: `Professional post by @${post.authorProfileHandle}.`, alternates: { canonical: path, types: publicMarkdownAlternate(post.markdownUrl) }, openGraph: { type: "article", title: post.title, description: `Professional post by @${post.authorProfileHandle}.`, url: path, publishedTime: post.publishedAt } };
  } catch { return { title: "Professional post" }; }
}

export default async function PostPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let post;
  try { post = await getPost(id); }
  catch (error) { if (error instanceof ApiRequestError && error.code === "not_found") notFound(); throw error; }
  return <PublicPostPage post={post} />;
}
