<script lang="ts">
	import type { ArtifactStatus } from '#lib/chat/types.ts';

	let { status }: { status: ArtifactStatus } = $props();
	const labels: Record<ArtifactStatus, string> = {
		draft: 'Needs information',
		ready: 'Ready for review',
		executing: 'In progress',
		completed: 'Completed',
		failed: 'Failed',
		denied: 'Denied',
		superseded: 'Superseded',
		expired: 'Expired'
	};
</script>

<span
	class={[
		'inline-flex rounded-full border px-2 py-0.5 text-2xs font-semibold',
		status === 'failed'
			? 'border-destructive/30 bg-destructive/10 text-destructive'
			: status === 'ready'
				? 'border-primary/25 bg-accent/65 text-accent-foreground'
				: 'border-border bg-muted/55 text-muted-foreground'
	]}>{labels[status]}</span
>
