import { Moon, PanelLeftClose, PanelLeftOpen, Plus, ShieldCheck, Sun } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useTheme } from '@/contexts/theme';
import { cn } from '@/lib/utils';

interface Props {
  collapsed: boolean;
  onToggle: () => void;
  onNewInvestigation: () => void;
}

/** The left nav, sized and styled like cloud-portal's (64px collapsed, 240px open). */
export function Sidebar({ collapsed, onToggle, onNewInvestigation }: Props) {
  const { theme, toggle } = useTheme();
  return (
    <nav
      className={cn(
        'flex shrink-0 flex-col border-r border-border-tertiary bg-surface-secondary transition-[width]',
        collapsed ? 'w-16' : 'w-60'
      )}
      aria-label="Main"
    >
      <div className={cn('flex h-14 items-center gap-2 px-4', collapsed && 'justify-center px-0')}>
        <ShieldCheck className="h-5 w-5 shrink-0" />
        {!collapsed && <span className="text-base font-semibold">Autosentry</span>}
      </div>
      <div className={cn('px-3', collapsed && 'px-2')}>
        <Button
          variant="outline"
          className={cn('w-full', collapsed && 'px-0')}
          onClick={onNewInvestigation}
          aria-label="New investigation"
        >
          <Plus className="h-4 w-4" />
          {!collapsed && 'New investigation'}
        </Button>
      </div>
      <div className="flex-1" />
      <div className={cn('flex gap-1 p-3', collapsed ? 'flex-col items-center' : 'items-center justify-between')}>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
      </div>
    </nav>
  );
}
