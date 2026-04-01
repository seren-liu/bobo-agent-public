import { forwardRef, useCallback, useImperativeHandle, useMemo, useRef, useState } from 'react';
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from '@gorhom/bottom-sheet';
import { useQueryClient } from '@tanstack/react-query';

import { ManualAddForm } from '@/components/ManualAddForm';
import { boboApi } from '@/lib/api';

export interface AddDrinkModalRef {
  open: (opts?: { consumedAt?: string }) => void;
  close: () => void;
}

export const AddDrinkModal = forwardRef<AddDrinkModalRef>((_, ref) => {
  const sheetRef = useRef<BottomSheet>(null);
  const queryClient = useQueryClient();
  const [consumedAt, setConsumedAt] = useState<string | undefined>(undefined);
  const snapPoints = useMemo(() => ['78%'], []);

  useImperativeHandle(ref, () => ({
    open: (opts) => {
      setConsumedAt(opts?.consumedAt);
      sheetRef.current?.expand();
    },
    close: () => sheetRef.current?.close(),
  }));

  const renderBackdrop = useCallback(
    (props: any) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} />
    ),
    []
  );

  return (
    <BottomSheet
      ref={sheetRef}
      index={-1}
      snapPoints={snapPoints}
      enablePanDownToClose
      backdropComponent={renderBackdrop}
      backgroundStyle={{ backgroundColor: '#FAFAFA', borderRadius: 28 }}
      handleIndicatorStyle={{ backgroundColor: '#D1D1D6', width: 40 }}
    >
      <BottomSheetView style={{ flex: 1, paddingHorizontal: 16, paddingTop: 8 }}>
        <ManualAddForm
          consumedAt={consumedAt}
          onSubmit={async (values) => {
            await boboApi.confirmRecords([
              {
                brand: values.brand,
                name: values.name,
                sugar: values.sugar,
                ice: values.ice,
                mood: values.mood,
                price: values.price,
                source: 'manual',
                consumed_at: values.consumedAt,
              },
            ]);
            queryClient.invalidateQueries({ queryKey: ['records', 'day'] });
            queryClient.invalidateQueries({ queryKey: ['records', 'calendar'] });
            queryClient.invalidateQueries({ queryKey: ['records', 'recent'] });
            queryClient.invalidateQueries({ queryKey: ['records', 'stats'] });
            queryClient.invalidateQueries({ queryKey: ['records', 'day-detail'] });
            values.reset();
          }}
          onSuccess={() => sheetRef.current?.close()}
        />
      </BottomSheetView>
    </BottomSheet>
  );
});

AddDrinkModal.displayName = 'AddDrinkModal';
