
Graph Builder(
	Size( 795, 646 ),
	Show Control Panel( 0 ),
	Fit to Window,
	Variables(
		X( :Block ),
		X( :Partition, Position( 1 ) ),
		X( :Module, Position( 1 ) ),
		Y( :"YieldLoss (%) - Avg"n ),
		Color( :Partition )
	),
	Elements( Points( X( 1 ), X( 2 ), X( 3 ), Y, Legend( 102 ) ) ),
	SendToReport(
		Dispatch(
			{},
			"Block",
			ScaleBox,
			{Label Row( 1, Show Major Grid( 1 ) ),
			Label Row( 2, Show Major Grid( 1 ) ),
			Label Row( 3, Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"YieldLoss (%) - Avg",
			ScaleBox,
			{Label Row( Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"400",
			ScaleBox,
			{Legend Model(
				102,
				Properties( 0, {Marker Size( 6 )}, Item ID( "ARR", 1 ) ),
				Properties( 1, {Marker Size( 6 )}, Item ID( "ASL", 1 ) ),
				Properties( 2, {Marker Size( 6 )}, Item ID( "ASR", 1 ) ),
				Properties( 3, {Marker Size( 6 )}, Item ID( "BSL", 1 ) ),
				Properties( 4, {Marker Size( 6 )}, Item ID( "BSR", 1 ) ),
				Properties( 5, {Marker Size( 6 )}, Item ID( "BUS_SSC", 1 ) ),
				Properties( 6, {Marker Size( 6 )}, Item ID( "BUS_UTC", 1 ) ),
				Properties( 7, {Marker Size( 6 )}, Item ID( "CBOPAIRAS", 1 ) ),
				Properties( 8, {Marker Size( 6 )}, Item ID( "CBOPAIRBS0", 1 ) ),
				Properties( 9, {Marker Size( 6 )}, Item ID( "CBOPAIRBS1", 1 ) ),
				Properties( 10, {Marker Size( 6 )}, Item ID( "CCU", 1 ) ),
				Properties( 11, {Marker Size( 6 )}, Item ID( "CHANAS", 1 ) ),
				Properties( 12, {Marker Size( 6 )}, Item ID( "CHANBS", 1 ) ),
				Properties( 13, {Marker Size( 6 )}, Item ID( "DLVR_ATOM", 1 ) ),
				Properties( 14, {Marker Size( 6 )}, Item ID( "DLVR_ICORE0", 1 ) ),
				Properties( 15, {Marker Size( 6 )}, Item ID( "DLVR_ICORE1", 1 ) ),
				Properties( 16, {Marker Size( 6 )}, Item ID( "DLVR_RING", 1 ) ),
				Properties( 17, {Marker Size( 6 )}, Item ID( "DMU", 1 ) ),
				Properties( 18, {Marker Size( 6 )}, Item ID( "EXE", 1 ) ),
				Properties( 19, {Marker Size( 6 )}, Item ID( "FE", 1 ) ),
				Properties( 20, {Marker Size( 6 )}, Item ID( "FEC", 1 ) ),
				Properties( 21, {Marker Size( 6 )}, Item ID( "FLATCORE", 1 ) ),
				Properties( 22, {Marker Size( 6 )}, Item ID( "FMAV0", 1 ) ),
				Properties( 23, {Marker Size( 6 )}, Item ID( "FMAV1", 1 ) ),
				Properties( 24, {Marker Size( 6 )}, Item ID( "FOV0", 1 ) ),
				Properties( 25, {Marker Size( 6 )}, Item ID( "FOV1", 1 ) ),
				Properties( 26, {Marker Size( 6 )}, Item ID( "FPC", 1 ) ),
				Properties( 27, {Marker Size( 6 )}, Item ID( "FUSE", 1 ) ),
				Properties( 28, {Marker Size( 6 )}, Item ID( "IEC", 1 ) ),
				Properties( 29, {Marker Size( 6 )}, Item ID( "MEC", 1 ) ),
				Properties( 30, {Marker Size( 6 )}, Item ID( "MEU", 1 ) ),
				Properties( 31, {Marker Size( 6 )}, Item ID( "MISC", 1 ) ),
				Properties( 32, {Marker Size( 6 )}, Item ID( "MLC", 1 ) ),
				Properties( 33, {Marker Size( 6 )}, Item ID( "MSID", 1 ) ),
				Properties( 34, {Marker Size( 6 )}, Item ID( "OOO_INT", 1 ) ),
				Properties( 35, {Marker Size( 6 )}, Item ID( "OOO_VEC", 1 ) ),
				Properties( 36, {Marker Size( 6 )}, Item ID( "PM", 1 ) ),
				Properties( 37, {Marker Size( 6 )}, Item ID( "PMH", 1 ) ),
				Properties( 38, {Marker Size( 6 )}, Item ID( "SANTA", 1 ) )
			)}
		),
		Dispatch(
			{},
			"graph title",
			TextEditBox,
			{Set Text( "Partition YieldLoss Analysis" )}
		),
		Dispatch( {}, "Y title", TextEditBox, {Set Text( "YieldLoss (%)" )} )
	)
);

## FAil Summary Plot
Graph Builder(
	Size( 849, 646 ),
	Show Control Panel( 0 ),
	Fit to Window,
	Variables(
		X( :Block ),
		X( :Partition, Position( 1 ) ),
		Y( :"YieldLoss (%)"n ),
		Y( :"Target YieldLoss (%)"n, Position( 1 ) )
	),
	Elements( Points( X( 1 ), X( 2 ), Y( 1 ), Y( 2 ), Legend( 102 ) ) ),
	SendToReport(
		Dispatch(
			{},
			"Block",
			ScaleBox,
			{Label Row( 1, Show Major Grid( 1 ) ),
			Label Row( 2, Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"YieldLoss (%)",
			ScaleBox,
			{Label Row( Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"400",
			ScaleBox,
			{Legend Model(
				102,
				Properties( 0, {Marker Size( 6 )}, Item ID( "YieldLoss (%)", 1 ) ),
				Properties(
					1,
					{Marker( "Triangle" ), Marker Size( 6 )},
					Item ID( "Target YieldLoss (%)", 1 )
				)
			)}
		),
		Dispatch(
			{},
			"graph title",
			TextEditBox,
			{Set Text( "Partition YieldLoss Analysis" )}
		),
		Dispatch( {}, "Y title", TextEditBox, {Set Text( "YieldLoss (%)" )} )
	)
);



Graph Builder(
	Size( 795, 646 ),
	Show Control Panel( 0 ),
	Fit to Window,
	Variables(
		X( :Block ),
		X( :Partition, Position( 1 ) ),
		X( :Module, Position( 1 ) ),
		Y( :"YieldLoss (%) - Avg"n ),
		Color( :Partition )
	),
	Elements( Points( X( 1 ), X( 2 ), X( 3 ), Y, Legend( 102 ) ) ),
	SendToReport(
		Dispatch(
			{},
			"Block",
			ScaleBox,
			{Label Row( 1, Show Major Grid( 1 ) ),
			Label Row( 2, Show Major Grid( 1 ) ),
			Label Row( 3, Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"YieldLoss (%) - Avg",
			ScaleBox,
			{Label Row( Show Major Grid( 1 ) )}
		),
		Dispatch(
			{},
			"400",
			ScaleBox,
			{Legend Model(
				102,
				Properties( 0, {Marker Size( 6 )}, Item ID( "ARR", 1 ) ),
				Properties( 1, {Marker Size( 6 )}, Item ID( "ASL", 1 ) ),
				Properties( 2, {Marker Size( 6 )}, Item ID( "ASR", 1 ) ),
				Properties( 3, {Marker Size( 6 )}, Item ID( "BSL", 1 ) ),
				Properties( 4, {Marker Size( 6 )}, Item ID( "BSR", 1 ) ),
				Properties( 5, {Marker Size( 6 )}, Item ID( "BUS_SSC", 1 ) ),
				Properties( 6, {Marker Size( 6 )}, Item ID( "BUS_UTC", 1 ) ),
				Properties( 7, {Marker Size( 6 )}, Item ID( "CBOPAIRAS", 1 ) ),
				Properties( 8, {Marker Size( 6 )}, Item ID( "CBOPAIRBS0", 1 ) ),
				Properties( 9, {Marker Size( 6 )}, Item ID( "CBOPAIRBS1", 1 ) ),
				Properties( 10, {Marker Size( 6 )}, Item ID( "CCU", 1 ) ),
				Properties( 11, {Marker Size( 6 )}, Item ID( "CHANAS", 1 ) ),
				Properties( 12, {Marker Size( 6 )}, Item ID( "CHANBS", 1 ) ),
				Properties( 13, {Marker Size( 6 )}, Item ID( "DLVR_ATOM", 1 ) ),
				Properties( 14, {Marker Size( 6 )}, Item ID( "DLVR_ICORE0", 1 ) ),
				Properties( 15, {Marker Size( 6 )}, Item ID( "DLVR_ICORE1", 1 ) ),
				Properties( 16, {Marker Size( 6 )}, Item ID( "DLVR_RING", 1 ) ),
				Properties( 17, {Marker Size( 6 )}, Item ID( "DMU", 1 ) ),
				Properties( 18, {Marker Size( 6 )}, Item ID( "EXE", 1 ) ),
				Properties( 19, {Marker Size( 6 )}, Item ID( "FE", 1 ) ),
				Properties( 20, {Marker Size( 6 )}, Item ID( "FEC", 1 ) ),
				Properties( 21, {Marker Size( 6 )}, Item ID( "FLATCORE", 1 ) ),
				Properties( 22, {Marker Size( 6 )}, Item ID( "FMAV0", 1 ) ),
				Properties( 23, {Marker Size( 6 )}, Item ID( "FMAV1", 1 ) ),
				Properties( 24, {Marker Size( 6 )}, Item ID( "FOV0", 1 ) ),
				Properties( 25, {Marker Size( 6 )}, Item ID( "FOV1", 1 ) ),
				Properties( 26, {Marker Size( 6 )}, Item ID( "FPC", 1 ) ),
				Properties( 27, {Marker Size( 6 )}, Item ID( "FUSE", 1 ) ),
				Properties( 28, {Marker Size( 6 )}, Item ID( "IEC", 1 ) ),
				Properties( 29, {Marker Size( 6 )}, Item ID( "MEC", 1 ) ),
				Properties( 30, {Marker Size( 6 )}, Item ID( "MEU", 1 ) ),
				Properties( 31, {Marker Size( 6 )}, Item ID( "MISC", 1 ) ),
				Properties( 32, {Marker Size( 6 )}, Item ID( "MLC", 1 ) ),
				Properties( 33, {Marker Size( 6 )}, Item ID( "MSID", 1 ) ),
				Properties( 34, {Marker Size( 6 )}, Item ID( "OOO_INT", 1 ) ),
				Properties( 35, {Marker Size( 6 )}, Item ID( "OOO_VEC", 1 ) ),
				Properties( 36, {Marker Size( 6 )}, Item ID( "PM", 1 ) ),
				Properties( 37, {Marker Size( 6 )}, Item ID( "PMH", 1 ) ),
				Properties( 38, {Marker Size( 6 )}, Item ID( "SANTA", 1 ) )
			)}
		),
		Dispatch(
			{},
			"graph title",
			TextEditBox,
			{Set Text( "Partition YieldLoss Analysis" )}
		),
		Dispatch( {}, "Y title", TextEditBox, {Set Text( "YieldLoss (%)" )} )
	)
);





