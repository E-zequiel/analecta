import path from 'path';

let vaultPath: string | null = null;

export function setVaultPath(p: string): void {
	vaultPath = path.resolve(p);
}

export function getVaultPath(): string | null {
	return vaultPath;
}

/**
 * Throws 'path outside vault' if the resolved filePath is not within the vault directory.
 * Must be called for every read-file / write-file IPC handler argument.
 */
export function assertVaultPath(filePath: string): void {
	if (!vaultPath) throw new Error('vault path not set');
	const resolved = path.resolve(filePath);
	if (resolved !== vaultPath && !resolved.startsWith(vaultPath + path.sep)) {
		throw new Error('path outside vault');
	}
}

/** No vault restriction — only validates that the string is non-empty. Used for file-exists. */
export function assertExistsPath(filePath: string): void {
	if (typeof filePath !== 'string' || !filePath.trim()) {
		throw new Error('invalid path');
	}
}
